
from amfpacket import decode_amf_packet, encode_amf_packet
from log import simple_log as l
from stream_io import FileStream, MemoryStream, ReadIncomplete

from time import time as now, sleep


def fill_in_constants_mapping(a):
	CLASSNAME_TO_ID = dict([(x, getattr(a, x)) for x in dir(a) if isinstance(getattr(a, x), int)])
	ID_TO_CLASSNAME = dict([(getattr(a, x), x) for x in dir(a) if isinstance(getattr(a, x), int)])
	a.CLASSNAME_TO_ID, a.ID_TO_CLASSNAME = CLASSNAME_TO_ID, ID_TO_CLASSNAME
	
	def id_to_name(id):
		if id in ID_TO_CLASSNAME:
			return ID_TO_CLASSNAME[id]
		return "UNKNOWN-ID: %r" % (id,) if id != None else "None"
	def name_to_id(name):
		if name in CLASSNAME_TO_ID:
			return CLASSNAME_TO_ID[name]
		return "UNKNOWN-NAME: %r" % (name,) if name != None else "None"
	a.id_to_name = staticmethod(id_to_name)
	a.name_to_id = staticmethod(name_to_id)

def process_class(a):
	"Monkey-patch helper class methods on all inner classes"
	[fill_in_constants_mapping(getattr(a, x)) for x in dir(a) if isinstance(getattr(a, x), type) and x != '__class__']

def reverse_mapping(map):
	return dict((value, key) for key, value in map.iteritems()) 



class MachineryError(Exception): pass

class FlvConstants(object):
	class Type(object):
		AUDIO, VIDEO, SCRIPT = 8, 9, 18
	class SoundFormat(object):
		PCM_PLATFORM_ENDIAN, ADPCM, MP3, PCM_LITTLE_ENDIAN, NELLYMOSER_16KHZ, \
		NELLYMOSER_8KHZ, NELLYMOSER, G711_A_LAW, G711_MU_LAW = range(9)
		# AAC, SPEEX = 10, 12
		AAC, SPEEX = 10, 11 ## in official spec 11 is SPEEX
		MP3_8KHZ, DEVICE_SPECIFIC = 14, 16
	class SoundRate(object):
		KHZ_5_5, KHZ_11, KHZ_22, KHZ_44 = range(4)
	class SoundSize(object):
		SIZE_8_BIT, SIZE_16_BIT = range(2)
	class SoundType(object):
		MONO, STEREO = range(2)
	class AACPacketType(object):
		SEQUENCE_HEADER, RAW = range(2)
	class CodecId(object):
		CODEC_ID_JPEG, CODEC_ID_H263, CODEC_ID_SCREEN_VIDEO, CODEC_ID_VP6, CODEC_ID_VP6_WITH_ALPHA, \
		CODEC_ID_SCREEN_VIDEO_V2, CODEC_ID_H264 = range(1, 8)
	class FrameType(object):
		KEYFRAME, INTERFRAME, DISPOSABLE_INTERFRAME, GENERATED_KEYFRAME, INFO_FRAME = range(1, 6)
	class AVCPacketType(object):
		SEQUENCE_HEADER, NALU, END_OF_SEQUENCE = range(3)
	class AVCNalunitType(object):
		UNSPECIFIED = 0
		CODED_SLICE = 1
		Data_Partition_A = 2
		Data_Partition_B = 3
		Data_Partition_C = 4
		IDR = 5
		SEI = 6
		SPS = 7
		PPS = 8
		ACCESS_UNIT_DELIMITER = 9
		END_OF_SEQUENCE = 10
		END_OF_STREAM = 11
		FILTER_DATA = 12


process_class(FlvConstants)

# from weakrefset import WeakSet
# tag_debug = WeakSet()

class Tag(object):
	def __init__(self, timestamp, previous_tag_size, last_timestamp=None, size=0, sequence=None):
		self.timestamp = timestamp
		self.previous_tag_size = previous_tag_size
		self.sequence = sequence
		# self.timespan = timestamp - last_timestamp if last_timestamp != None else None
		self.timespan = timestamp - last_timestamp if last_timestamp != None else 0
		self.size = size
		# tag_debug.add(self)
	def clone(self, new_timestamp):
		ret = Tag(self.timestamp, self.previous_tag_size, None, self.size)
		ret.timespan = self.timespan
		return ret
	def l(self):
		return dict(
			timestamp=self.timestamp,
			timespan=self.timespan,
			previous_tag_size=self.previous_tag_size,
			)
		# ret = self.__dict__.copy()
		# ret['tag'] = self.__class__.__name__
		# return ret

class AudioTag(Tag):
	def clone(self, new_timestamp):
		ret = Tag.clone(self, new_timestamp)
		ret.sound_format, ret.sound_rate, ret.sound_size, ret.sound_type, ret.aac_packet_type, ret.data = \
			self.sound_format, self.sound_rate, self.sound_size, self.sound_type, self.aac_packet_type, self.data
		return ret, new_timestamp + self.timespan
	def len(self):
		if self.sound_format == FlvConstants.SoundFormat.AAC:
			return len(self.data) + 2
		return len(self.data) + 1
	def write_to_stream(self, stream):
		stream.wu8( 
			((self.sound_format & 0x0F) << 4) | 
			((self.sound_rate & 0x03) << 2) | 
			((self.sound_size & 0x01) << 1) | 
			(self.sound_type & 1) 
			)
		if self.sound_format == FlvConstants.SoundFormat.AAC:
			stream.wu8(self.aac_packet_type)
			stream.write(self.data)
			return len(self.data) + 2
		stream.write(self.data)
		return len(self.data) + 1
	def l(self):
		ret = Tag.l(self)
		ret.update(dict(
			format=FlvConstants.SoundFormat.id_to_name(self.sound_format),
			rate=FlvConstants.SoundRate.id_to_name(self.sound_rate),
			size=FlvConstants.SoundSize.id_to_name(self.sound_size),
			type=FlvConstants.SoundType.id_to_name(self.sound_type),
			aac_packet_type=FlvConstants.AACPacketType.id_to_name(self.aac_packet_type),
			data_len=len(self.data),
			))
		return ret

class VideoTag(Tag):
	def clone(self, new_timestamp):
		ret = Tag.clone(self, new_timestamp)
		ret.frame_type, ret.codec_id, ret.avc_packet_type, ret.composition_time, ret.data = \
			self.frame_type, self.codec_id, self.avc_packet_type, self.composition_time, self.data
		ret.nal_unit_type = self.nal_unit_type
		return ret, new_timestamp + self.timespan
	def len(self):
		if self.codec_id == FlvConstants.CodecId.CODEC_ID_H264:
			return len(self.data) + 5
		return len(self.data) + 1
	def calculate_type(self):
		if len(self.data) > 4:
			first_byte = self.data[4]
			if isinstance(first_byte, str): first_byte = ord(first_byte)
			else: first_byte = int(first_byte)
			self.nal_unit_type = first_byte & 0x1F
		else:
			self.nal_unit_type = 0
	def write_to_stream(self, stream):
		stream.wu8(((self.frame_type & 0x0F) << 4) | (self.codec_id & 0x0F))
		if self.codec_id == FlvConstants.CodecId.CODEC_ID_H264:
			stream.wu8(self.avc_packet_type)
			stream.wu24(self.composition_time)
			stream.write(self.data)
			return len(self.data) + 5
		stream.write(self.data)
		return len(self.data) + 1
	def l(self):
		ret = Tag.l(self)
		ret.update(dict(
			data_len=len(self.data),
			codec=FlvConstants.CodecId.id_to_name(self.codec_id),
			frame_type=FlvConstants.FrameType.id_to_name(self.frame_type),
			avc_packet_type=FlvConstants.AVCPacketType.id_to_name(self.avc_packet_type),
			composition_time=self.composition_time,
			type=self.nal_unit_type,
			_=str(self.data[:8]).encode('hex'),
			))
		return ret
	
	def _g(self, name):
		if hasattr(self, name):
			return getattr(self, name)
		else:
			return "NIL-{0}".format(name)

	def __str__(self):
		return "<V type={type} ts={ts:.2f} len={l} c={c} frame={f} packet={p} ct={ct}/>".format(
			type=self._g("nal_unit_type"),
			ts=self.timestamp,
			c=FlvConstants.CodecId.id_to_name(self._g("codec_id")),
			f=FlvConstants.FrameType.id_to_name(self._g("frame_type")),
			p=FlvConstants.AVCPacketType.id_to_name(self._g("avc_packet_type")),
			l=len(self.data),
			ct=self._g("composition_time"),
			)
	__repr__ = __str__

class ScriptTag(Tag):
	def clone(self, new_timestamp):
		ret = Tag.clone(self, new_timestamp)
		ret.data, ret.raw_data = self.data, self.raw_data
		return ret, new_timestamp + self.timespan
	def len(self):
		## TODO: here we are encodeing for len, fix it 
		return len(encode_amf_packet(self.data))
	def write_to_stream(self, stream):
		wiredata = encode_amf_packet(self.data)
		stream.write( wiredata )
		return len(wiredata)
	def l(self):
		ret = Tag.l(self)
		ret.update(dict(data=self.data))
		return ret

TYPE_TAGS = {
	8 : AudioTag,
	9 : VideoTag,
	18 : ScriptTag,
	}
TAGS_TYPE = reverse_mapping(TYPE_TAGS)

class UnreckognizedTag(Exception):
	"Reader does not support this tag"
	def __init__(self, previous_tag_size, timestamp, type, data):
		self.previous_tag_size, self.timestamp, self.type, self.data = previous_tag_size, timestamp, type, data
class UnknownTag(Exception):
	"Writer does not support this tag"

class MalformedHeader(Exception): pass
class MalformedCotainer(Exception): pass

class HeaderDetails(object):
	def __init__(self, audio_present, video_present, version):
		self.audio_present, self.video_present, self.version = audio_present, video_present, version

class FLV(object):
	"""
		In playback, the time sequencing of FLV tags depends on the FLV timestamps only. 
		Any timing mechanisms built into the payload data format shall be ignored.
		
	"""
	@staticmethod
	def write_header(stream, audio_present=True, video_present=True, version=1):
		stream.write('FLV')
		stream.wu8(version)
		stream.wu8( (4 if audio_present else 0) | (1 if video_present else 0) )
		stream.wu32(9)
		stream.wu32(0) # PreviousTagSize0 UI32 Always 0
		# Size of previous tag, including its header, in bytes. For FLV version
		# 1, this value is 11 plus the DataSize of the previous tag.
	
	@staticmethod
	def write_tag(stream, tag, stream_timestamp, stream_id=0):
		"Does not support encryption yet"
		if tag.__class__ not in TAGS_TYPE:
			raise UnknownTag(tag)
		datasize = tag.len()
		stream_timestamp += tag.timespan if tag.timespan != None else stream_timestamp
		stream.wu8(TAGS_TYPE[tag.__class__])
		stream.wu24(datasize)
		stream.wu24(int(stream_timestamp) & 0xFFFFFF)
		stream.wu8((int(stream_timestamp) & 0xFF000000) >> 24)
		stream.wu24(stream_id)
		
		## TODO: had to remove machinery check bc of stream manipulation
		# bytes_start = stream.tell()
		tag.write_to_stream(stream)
		# diff = stream.tell() - bytes_start
		# if diff != datasize:
			# raise MachineryError("%s tag write check failed %s instead %s" % (tag.__class__.__name__, diff, datasize))
		stream.wu32(datasize + 11)
		return stream_timestamp
	
	@staticmethod
	def validate(flv_tags):
		""" TODO: find all possibile errors on this flv container 
		"""



import sys
import struct
import socket
import picamera
from time import time as now, sleep
from threading import Thread
from Queue import Queue, Empty
from traceback import print_exc, format_stack

from stream_io import FileStream, SocketStream
from log import simple_log as l
from tag_writter import FLV, UnreckognizedTag, VideoTag, FlvConstants


H264_NALU_TYPE_TO_NUMBER = {
	"UNSPECIFIED"           : 0,
	"SLICE_NON_IDR"         : 1,
	"DATA_PARTITION_A"      : 2,
	"DATA_PARTITION_B"      : 3,
	"DATA_PARTITION_C"      : 4,
	"IDR" : 5,
	"SEI" : 6,
	"SPS" : 7,
	"PPS" : 8,
	"AUD" : 9,
	"END_OF_SEQUENCE"       : 10,
	"END_OF_STREAM"         : 11,
	"FILTER_DATA"           : 12,
	"SPS_EXTENSION"         : 13,
	"PREFIX_NAL_UNIT"       : 14,
	"SUBSET_SPS"            : 15,
	"CODED_SLICE_OF_AN_AUXILIARY_CODED_PICTURE_WITHOUT_PARTITIONING" : 19,
	"CODED_SLICE_EXTENSION" : 20,
}
NUMBER_TO_H264_NALU_TYPE = {}
for k, v in H264_NALU_TYPE_TO_NUMBER.items(): NUMBER_TO_H264_NALU_TYPE[v] = k

NALS_NEEDING_GROUPING = [
	H264_NALU_TYPE_TO_NUMBER["SPS"], 
	H264_NALU_TYPE_TO_NUMBER["PPS"], 
	H264_NALU_TYPE_TO_NUMBER["SEI"], 
	]


class OperationSpecification(object):
	def __init__(self, tb, operation, args=(), kwargs={}):
		self.tb, self.operation, self.args, self.kwargs = tb, operation, args, kwargs

class OffloadedQueue(Thread):
	def __init__(self):
		Thread.__init__(self)
		self._queue = Queue()
		self._exception_queue = Queue()
		self.setDaemon(True)
		self.start()

	def schedule(self, f, *a, **kw):
		try:
			raise self._exception_queue.get_nowait()
		except Empty, e:
			pass
		tb = ''.join(format_stack())
		self._queue.put(OperationSpecification(tb, f, a, kw))

	def run(self):
		while 1:
			op = self._queue.get()
			try:
				op.operation(*op.args, **op.kwargs)
				# print "offload exec op={0}".format(op.operation)
			except Exception, e:
				e.message = " origin: {0} \n{1}".format(op.tb, e.message)
				self._exception_queue.put(e)


class VideoCapture:
	def __init__(self, write_frame, picamera, one_frame_time_ms=16):
		self.picamera = picamera
		self._write = self.handle_sequence_header
		self.working = True
		self.write_frame = write_frame
		self.name = "streaming.h264"
		self.one_frame_time_ms = one_frame_time_ms
		self._last_tag = None
		self._grouping_nals = []
		self.timestamp = 0
		self.previous_tag_size = 0
		self.last_timestamp = 0
		self.__sps = None
		self.__pps = None
		self.__start_time = None

	def annexb_to_avcc(self, annexb_nal):
		prefix = struct.pack("!I", len(annexb_nal) - 4)
		return prefix + annexb_nal[4:]

	def handle_sequence_header(self, annexb_nal, is_keyframe, timestamp):
		if self.__start_time is None:  self.__start_time = now()

		avcc_nal = self.annexb_to_avcc(annexb_nal)
		nal_unit_type = ord(avcc_nal[4]) & 0x1F
		l.d("sequence NAL", type=nal_unit_type, size=len(annexb_nal), hex=annexb_nal[:8].encode("hex"))
		if nal_unit_type == 7:
			l.d(" storing SPS", hex=annexb_nal.encode("hex"))
			self.__sps = avcc_nal
		elif nal_unit_type == 8:
			assert self.__sps is not None, "SPS shall arrive before PPS"
			l.d(" storing PPS", hex=annexb_nal.encode("hex"))
			self.__pps = avcc_nal
		elif nal_unit_type == 5:
			assert self.__sps is not None, "SPS shall arrive before IDR"
			assert self.__pps is not None, "PPS shall arrive before IDR"
			payload = "".join((self.__sps, self.__pps, avcc_nal))
			# construct DCR - the sequence start
			max_profile_idc = ord(self.__sps[5])
			profile_compatibility = ord(self.__sps[6]) # constraint_set
			max_level_idc = ord(self.__sps[7])
			l.d("info", profile=max_profile_idc, level=max_level_idc, constraint=("{0:X}".format(max_level_idc)))

			dcr = []
			dcr.append(struct.pack("!BBBBBBH", 
				1, max_profile_idc, profile_compatibility, max_level_idc,
				0xFF, # length size minus one 3
				0xE1, # 1 SPS
				len(self.__sps) -4
				))
			dcr.append(self.__sps[4:])
			dcr.append(struct.pack("!BH", 1, len(self.__pps) -4))
			dcr.append(self.__pps[4:])
			dcr_payload = "".join(dcr)
			l.d(" dcr_payload", hex=dcr_payload.encode("hex"))
			# write DCR
			tag = VideoTag(0, 0, 0, len(dcr_payload))
			tag.stream_id = 0
			tag.codec_id = FlvConstants.CodecId.CODEC_ID_H264
			tag.frame_type = FlvConstants.FrameType.KEYFRAME
			tag.avc_packet_type = FlvConstants.AVCPacketType.SEQUENCE_HEADER
			tag.composition_time = 0
			tag.data = dcr_payload
			self.write_frame(tag, 0, 0)
			# self.previous_tag_size = tag.len()
			# write IDR
			tag = VideoTag(0, self.previous_tag_size, 0, len(dcr_payload))
			tag.stream_id = 0
			tag.codec_id = FlvConstants.CodecId.CODEC_ID_H264
			tag.frame_type = FlvConstants.FrameType.KEYFRAME
			tag.avc_packet_type = FlvConstants.AVCPacketType.NALU
			tag.composition_time = 0 #int(self.one_frame_time_ms)
			tag.data = payload
			self.write_frame(tag, 0, 0)
			# advance state
			self.last_timestamp = 0
			self.timestamp += self.one_frame_time_ms
			# self.previous_tag_size = tag.len()
			# our work is done
			self._write = self.handle_frames
		else:
			raise Exception("NYI nal unit type={0}".format(nal_unit_type))

	def handle_frames(self, annexb_nal, is_keyframe, timestamp):
		# self.timestamp = (now() - self.__start_time) * 1000.0
		if timestamp is not None:
			self.timestamp = timestamp / 1000.0
		# else:
		#	## This happens on SPS PPS nals
		# 	print "missing TS"
		avcc_nal = self.annexb_to_avcc(annexb_nal)
		nal_unit_type = ord(avcc_nal[4]) & 0x1F
		# print "NAL type={2} size={1} ts={3} psize={4} 0x{0}\tCPU:{cpu}\tGPU:{gpu}".format(annexb_nal[:8].encode("hex"), len(annexb_nal), nal_unit_type, self.timestamp, self.previous_tag_size, cpu=self.cpu, gpu=self.gpu)
		
		# skip it
		if nal_unit_type in NALS_NEEDING_GROUPING: 
			return
		payload = avcc_nal
		tag = VideoTag(self.timestamp, self.previous_tag_size, self.last_timestamp, len(payload))
		tag.stream_id = 0
		tag.codec_id = FlvConstants.CodecId.CODEC_ID_H264
		# tag.frame_type = FlvConstants.FrameType.KEYFRAME if nal_unit_type == 5 else FlvConstants.FrameType.INTERFRAME
		tag.frame_type = FlvConstants.FrameType.KEYFRAME if is_keyframe else FlvConstants.FrameType.INTERFRAME
		tag.avc_packet_type = FlvConstants.AVCPacketType.NALU
		tag.composition_time = 0 # TODO !
		tag.data = payload
		# write to file
		self.write_frame(tag, self.timestamp, 0)
		# advance state
		self.last_timestamp = self.timestamp
		self.previous_tag_size = tag.len()

	def write(self, annexb_nal):
		try:
			# index, is_keyframe, frame_size, video_size, split_size, timestamp = self.picamera.frame
			frame_info = self.picamera.frame
			self._write(annexb_nal, frame_info.keyframe, frame_info.timestamp)
		except:
			print_exc()
			sys.stderr.flush()
			self.working = False

class Publisher:
	def __init__(self, host="h264.me", port=1901, stream_name=None, capture_resolution=(640, 480), output_to_file=False, rotation=90, bitrate=1000000):
		self.capture_resolution = capture_resolution
		self.rotation = rotation
		self.bitrate = bitrate
		self.offload_queue = OffloadedQueue()
		if stream_name is None:
			stream_name = "pi-" + now()
		if output_to_file:
			self.stream = FileStream("{0}.flv".format(stream_name), "wb")
		else:
			s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			s.connect((host, port))
			self.stream = SocketStream(s)
			self.offload_queue.schedule(
				self.stream.write, 
				"POST /publishing/{0} HTTP/1.1\r\nContent-Type: video/x-flv\r\nConnection: close\r\n\r\n".format(stream_name)
			)
		self.picamera = None

	def write_video_frame(self, tag, timestamp, stream_id=0):
		"Implementing one frame buffer to order frames by timestamp"
		# l.d("pushing", frame=tag)
		self.offload_queue.schedule(FLV.write_tag, self.stream, tag, timestamp, stream_id)

	def run(self):
		with picamera.PiCamera() as camera:
			camera.resolution = self.capture_resolution
			camera.rotation = self.rotation
			camera.start_preview()
			l.d("preview started")
			self.offload_queue.schedule(FLV.write_header, self.stream, video_present=True, audio_present=False)
			sleep(1)
			video_capture = VideoCapture(self.write_video_frame, camera)
			camera.start_recording(video_capture, bitrate=self.bitrate, profile="constrained")
			try:
				while 1:
					camera.wait_recording(1)
			except KeyboardInterrupt, e:
				pass
			camera.stop_preview()
			camera.stop_recording()


if __name__ == "__main__":
	stream_name = None
	if len(sys.argv) > 1:
		stream_name = sys.argv[1]
	publisher = Publisher(stream_name=stream_name)
	publisher.run()





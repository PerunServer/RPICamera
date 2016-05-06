
from amfast.encoder import Encoder
from amfast.decoder import Decoder
from amfast.context import DecoderContext
from amfast.buffer import BufferError

from stream_io import MemoryStream

decoder = Decoder(amf3=False)
encoder = Encoder(amf3=False)

def decode_amf_packet(raw_packet):
	context = DecoderContext(MemoryStream(raw_packet), amf3=False, class_def_mapper=decoder.class_def_mapper)
	ret = []
	while context.buffer.tell() < len(raw_packet):
		ret.append( decoder.decode(context) );
	return ret

def encode_amf_packet(list_of_objects):
	return "".join([encoder.encode(element) for element in list_of_objects])





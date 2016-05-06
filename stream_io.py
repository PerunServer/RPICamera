
"""
Notes:
	- nasledjivanje:
		cesto otpakujem jednu strukturu iz streama koja onda odredi tip sub-strukture
		to sam sada nespretno resio u mp4 parseru preko metoda koje podrzavaju samo jedan ovakav stepen.
		
		- [ :( ] resenje generickom strukturom Box koji se dalje samo monkeypatchuje :(
		- resenje baznom klasom koja ima metode za kloniranje
			@classmethod
			def create_from(cls, parent_cell):
				return cls().inherit(parent_cell)
			def inherit(self, parent_cell):
				for attr, val in parent_cell.__dict__.items():
					if attr in parent_cell.private: continue
					setattr(self, val)
				return self

	- boxed cell 
		Procitani header odredjuje velicinu celije.
		Potrebno je prosiriti stream sa stackom dozvoljenog citanja
	
	- transformacije nad boxovima:
		- replace in current box 


"""


import struct
from io import BytesIO


DBG_IO = False

class CriticalError(Exception): pass

def truncate(data, max=100):
    return data and len(data)>max and data[:max] + '...(%d)'%(len(data),) or data

class ReadIncomplete(Exception): 
	def __init__(self, partial_data, missing_bytes):
		self.partial_data, self.missing_bytes = partial_data, missing_bytes
	def __str__(self):
		return "ReadIncomplete missing:%s partial:%r" % (self.missing_bytes, truncate(self.partial_data))
	__repr__ = __str__

class IOStream(object):
	"Basic Interface for stream"
	def close(self): pass
	def __del__(self): pass
	# def flush(self): pass
	def write(self, data): pass
	def read(self, size=None): pass 
	def read_exact(self, size): pass 
	# def peak(self, size): pass
	
	# def __init__(self):
		# self.__limits = []
	# def push_limit(self, size):
	# def pop_limit(self):
	
	
	def ru32le(stream):
		ret, = struct.unpack("<I", str(stream.read_exact(4)))
		return ret
	def ru32(stream):
		ret, = struct.unpack("!I", str(stream.read_exact(4)))
		return ret
	def ru24(stream):
		h, l = struct.unpack("!BH", str(stream.read_exact(3)))
		return (h << 16) + l
	def ru16(stream):
		ret, = struct.unpack("!H", str(stream.read_exact(2)))
		return ret
	def ru8(stream):
		ret, = struct.unpack("!B", str(stream.read_exact(1)))
		return ret
	def ru64(stream):
		ret, = struct.unpack("!Q", str(stream.read_exact(8)))
		return ret

	def wu32le(stream, val):
		stream.write(struct.pack("<I", val))
		return stream
	def wu32(stream, val):
		stream.write(struct.pack("!I", val))
		return stream
	def wu24(stream, val):
		stream.write(struct.pack("!BH", (val & 0xFF0000) >> 16, val & 0xFFFF))
		return stream
	def wu16(stream, val):
		stream.write(struct.pack("!H", val))
		return stream
	def wu8(stream, val):
		stream.write(struct.pack("!B", val))
		return stream
	def wu64(stream, val):
		stream.write(struct.pack("!Q", val))
		return stream

def StreamTemplate(io_backend):
	class StreamTemplateIncarnation(io_backend, IOStream):
		"Stream on top of %s" % (io_backend, )
		def __init__(self, *args, **kwargs):
			self.read_inclomplete = kwargs.get('read_inclomplete', None)
			kwargs.pop('read_inclomplete', None)
			io_backend.__init__(self, *args, **kwargs)
			self._read_p = self.tell()
			self._write_p = self.tell()
		
		def reset_append(self, chunk):
			"append chunk on unread data"
			if self.tell() != self._read_p: self.seek(self._read_p)
			unread = io_backend.read(self)
			self.seek(0)
			io_backend.truncate(self)
			if unread: 
				## this should not happen bc. reset_append comes after stream is consumed entirely
				io_backend.write(self, unread)
			io_backend.write(self, chunk)
			self.seek(0)
			self._read_p, self._write_p = self.tell(), self.tell()
		
		def write(self, data):
			if self.tell() != self._write_p: self.seek(self._write_p)
			try:
				return io_backend.write(self, data)
			finally:
				self._write_p = self.tell()

		def read(self, size=None, last_diff=None):
			if self.tell() != self._read_p: self.seek(self._read_p)
			try:
				ret = io_backend.read(self, size)
				if size is None: return ret
				diff = size - len(ret)
				if diff > 0: 
					if diff != last_diff and self.read_inclomplete:
						## call self.read_inclomplete
						self._read_p = self.tell()
						self.read_inclomplete(self, diff)
						return ret + self.read(diff, diff)
					else:
						raise ReadIncomplete(ret, diff)
				return ret
			finally:
				self._read_p = self.tell()
		
		read_exact = read
		
		def peak(self, size): 
			pos = self._read_p
			try:
				return self.read(size)
			finally:
				self._read_p = pos
		
		def rseek(self, *args):
			if self.tell() != self._read_p: self.seek(self._read_p)
			self.seek(*args)
			self._read_p = self.tell()
		def wseek(self, *args):
			if self.tell() != self._write_p: self.seek(self._write_p)
			self.seek(*args)
			self._write_p = self.tell()
	return StreamTemplateIncarnation

MemoryStream = StreamTemplate( BytesIO )

FileStream = StreamTemplate( file )

class SocketStream(IOStream):
	""" Stream on top of socket (gevent.socket)
		TODO: Buffering 
	"""
	def __init__(self, sock):
		self.s = sock
		self.r_buf = bytearray()
		# self.w_buf = bytearray()

	def write(self, data):
		if DBG_IO: ' [SocketStream] write:', repr(data)
		self.s.sendall(data)
	
	def read_exact(self, size):
		try:
			return SocketStream.read(self, size)
		except ReadIncomplete, fragment:
			if len(fragment.partial_data) == 0: raise
			return fragment.partial_data + self.read_exact(fragment.missing_bytes)
	
	def read(self, size):
		if size is None:
			raise CriticalError("Forbidden read to EOF on SocketStream")
			# try: return self.r_buf[:] + self.s.recv(size)
			# finally: self.r_buf[:] = []
		
		## maybe we have it buffered:
		if len(self.r_buf) >= size:
			ret = self.r_buf[:size]
			self.r_buf[:size] = []
			if DBG_IO: ' [SocketStream] returning from the buffer', repr(ret)
			return ret 
		
		left = size - len(self.r_buf)
		ret = self.s.recv(left)
		if DBG_IO: ' [SocketStream] recv:', repr(ret)
		diff = left - len(ret)
		try:
			ret = self.r_buf[:] + ret
			if DBG_IO: ' [SocketStream] read:', repr(ret)
			if diff > 0: raise ReadIncomplete(ret, diff)
			return ret
		finally:
			self.r_buf[:] = []

	def peak(self, size): 
		try:
			ret = self.read(size)
			self.r_buf += ret
			return ret
		except ReadIncomplete, fragment:
			self.r_buf += fragment.partial_data
			raise
		except Exception, e:
			## TODO: missing finnaly, how to handle unexpected exceptions ??
			raise CriticalError(e)

def substream(stream, size):
	return MemoryStream(stream.read_exact(size))














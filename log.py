
import sys, cStringIO, traceback
from traceback import print_exc
from time import time as now, ctime

class SimpleLog(object):
	tags = set()
	params = {}
	def nest(self, *new_tags, **new_params):
		"Creates new instance of specialized log, keeping existing tags & params"
		ret = SimpleLog()
		ret.tags = self.tags | set(new_tags)
		ret.params = self.params.copy()
		ret.params.update(new_params)
		return ret
	
	def __call__(self, event, _level_="d", **kwargs):
		# add all tags 
		kwargs.update(self.params)
		try:
			data = " ".join(
				# self.tags +
				[ctime(), _level_, event] +
				["%s=%r" % (key, val) for key, val in kwargs.items()]
				)
			print data 
		except:
			print_exc()
			
	
	def error(self, event, **kwargs):
		self.__call__(event, _level_="e", **kwargs)
	def info(self, event, **kwargs):
		self.__call__(event, _level_="i", **kwargs)
	def warn(self, event, **kwargs):
		self.__call__(event, _level_="w", **kwargs)
	def debug(self, event, **kwargs):
		self.__call__(event, _level_="d", **kwargs)
	def exception(self, description=None, **kwargs):
		txt = self.formatException(sys.exc_info())
		if description: self.error(description, **kwargs)
		self.error(txt, **kwargs)
	
	e, i, w, d = error, info, warn, debug

	def formatException(self, ei):
		sio = cStringIO.StringIO()
		traceback.print_exception(ei[0], ei[1], ei[2], None, sio)
		s = sio.getvalue()
		sio.close()
		if s[-1:] == "\n":
			s = s[:-1]
		return s

simple_log = SimpleLog()

if __name__ == '__main__':
	l = simple_log
	l.error("strange error occured", alex='car', log=3.141528)
	l.info("strange error occured", alex='car', log=3.141528)
	l.warn("strange error occured", alex='car', log=3.141528)
	l("all is fine now", alex='happy', log=3.141528)
	try:
		raise Exception("alex alex")
	except:
		l.exception('testing it')



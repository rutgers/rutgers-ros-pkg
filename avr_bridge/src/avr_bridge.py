#! /usr/bin/env python
#http://www.ibm.com/developerworks/linux/tutorials/l-pysocks/section4.html
# http://www.sics.se/~adam/uip/uip-1.0-refman/
#  python structured data  http://www.doughellmann.com/PyMOTW/struct/

#This file contains the library files for 

import roslib; roslib.load_manifest('avr_bridge')
import rospy
import serial 
import struct
import yaml
import threading
import std_msgs.msg
import StringIO
import time

"""
At start up I need to read through the message definitions, and generate
"""
debug_packets = False

class AvrBridge():
	"""
	"""
	def __init__(self, configFile = None):
		self.port = None
		self.services = {}
		self.subscribers = {} #key is topic name, val is subscriber
		self.publishers = {}# key topic name, val is publisher 
		self.topics = {} #key is topic name 
						 #contains msg constructor
		self.com_keys = {} #dict of com keys to topic names
							# com keys are integers generated by their
							# order in the config file
		self.topic_tags = {} #dict of topic names to packet topic tags
		
		self._id_counter = 0 #ID of topic in packet_header
		
		self.io_thread = threading.Thread(target= self.__io_update)
		self.io_thread.deamon = True
		self.__done = threading.Event()
		
		self.name = None
		
		#packet structures
		self.header_struct = struct.Struct('B B h') # packet_type topic_tag data_length

		if (configFile != None):
			self.parseConfig(configFile)
			
	def run(self):
		if (self.port == None):
			self.openDevice(self.portName)
		self.io_thread.start()
		
	def shutdown(self):
		self.__done.set()
		self.io_thread.join()
		self.port.close()
		
	def parseConfig(self, configFile):
		""" takes a file-like object of the configuration file
			parses it, and creates the com_tags
			
		"""
		self.config = yaml.load(configFile)
		
		#services get their topic ID first
		if self.config.has_key('service'):
			for topic in self.config['service']:
				#import that msg's python module
				msg_type = self.config['service'][topic]['type']
				module_name,  msg_name = msg_type.split('/')
				
				try:
					module = __import__( module_name +'.srv')
				except:
					roslib.load_manifest(module_name)
					module = __import__( module_name +'.srv')


				srv_module = getattr(module, 'srv')
				srv = getattr(msg_module, msg_name)
							
				self.addService(topic, srv)
		
		#subscribes must get their topic ID first
		if self.config.has_key('subscribe'):
			for topic in self.config['subscribe']:
				#import that msg's python module
				msg_type = self.config['subscribe'][topic]['type']
				module_name,  msg_name = msg_type.split('/')
				
				try:
					module = __import__( module_name +'.msg')
				except:
					roslib.load_manifest(module_name)
					module = __import__( module_name +'.msg')

				
				msg_module = getattr(module, 'msg')
				msg = getattr(msg_module, msg_name)
							
				self.addSubscriber(topic, msg)
		
		
		if self.config.has_key('publish'):
			for topic in self.config['publish']:
				#import that msg's python module
				msg_type = self.config['publish'][topic]['type']
				module_name,  msg_name = msg_type.split('/')
				

				try:
					module = __import__( module_name +'.msg')
				except:
					roslib.load_manifest(module_name)
					module = __import__( module_name +'.msg')

				msg_module = getattr(module, 'msg')
				msg = getattr(msg_module, msg_name)
				
				self.addPublisher(topic, msg)
						
	
		self.portName = self.config['port']
		
		
		

	def addSubscriber(self, topic, rtype):
		"""Subscribes to the topic and does the bridge book keeping
			@param topic : name of topic to which to subscribe 
			@param type  : topic type constructor
			@param ID  :  ID tag used for topic identification in serial packet
		"""
		
		self.subscribers[topic] = rospy.Subscriber(topic, rtype, lambda msg : self.subscriberCB(msg, topic))
		self.topics[topic] = rtype
		self.__addID(topic)

		
	def addPublisher(self, topic, rtype):
		"""Creates topic publisher for packets being set by the avr
			does internal com bookkeeping
			@param topic : name of topic to which to subscribe 
			@param type  : topic type constructor
			@param ID  :  ID tag used for topic identification in serial packet
		"""

		self.publishers[topic] = rospy.Publisher(topic, rtype)
		self.topics[topic] = rtype
		self.__addID(topic)

	def addService(self, topic, rtype, ID= None):
		#rospy.Service
		self.__addID(topic)
		
	def __addID(self, topic):
		#store com key value for compressed messaging ID
		self.com_keys[self._id_counter] = topic
		self.topic_tags[topic] = self._id_counter
		self._id_counter +=1		

	def openDevice(self, port):
		
		if (port.find('/dev/') == -1):
			port = '/dev/'+ port

		self.port = serial.Serial(port, 57600, timeout=0.1)
		time.sleep(2)
		self.portName = port
		self.port.flushOutput()
		self.port.flushInput()

	
	def __getPacket(self):
		if not self.port.isOpen():
			return None, None, 0, []
		header = self.port.read(4)
		
		if not (len(header) == 4) :
			return None, None, 0, []
		
		packet_type, topic_tag, data_length = self.header_struct.unpack(header)
		msg_data = self.port.read(data_length)
		return packet_type, topic_tag, data_length, msg_data
		
	def is_valid_packet(self, packet):
		packet_type, topic_tag, data_length, msg_data = packet
		if (packet_type == None):
			return False
		if (not self.com_keys.has_key(topic_tag) and not (packet_type == 255)):
			return False
		return True
	
	def __io_update(self):
		
		while not self.__done.isSet():
			packet  = self.__getPacket()
			packet_type, topic_tag, data_length, msg_data = packet
			#if (debug_packets):
			#	print packet
			if (self.is_valid_packet(packet)):
				rospy.logdebug("Packet recieved " + str(packet))
				# packet types
				# 0 avr is publishing
				# 1 avr is subscribing
				# 2 service request from outside
				# 3 service response from avr
					
				if packet_type == 0: #it was a published message
					topic = self.com_keys[topic_tag]
					msg = self.topics[topic]() #instantiate a msg object from the topic
					try:
						msg.deserialize(msg_data)
						
						if hasattr(msg, 'header'):
							msg.header.time = rospy.Time()
						
						self.publishers[topic].publish(msg)
					except Exception as e:
						print "Failed to deserialize topic ", topic
						print e
					
				if packet_type ==1:
					topic = self.com_keys[topic_tag]
					msg = self.topics[topic]() #instantiate a msg object from the topic
					msg.deserialize(msg_data)

					self.handle_service(msg, topic)
						
				if packet_type == 255:					
					name = std_msgs.msg.String()
					name.deserialize(msg_data)
					self.name = name.data
			time.sleep(0.01)

			

	def handle_service(self, msg, topic):
		pass
		
	def subscriberCB(self, msg, t):
		rospy.logdebug("topic : %s    msg:   %s"%(t,msg))
		self.sendAVR(msg, topic = t, rtype =0)
		
	def sendAVR(self, msg, topic = None, rtype = None, tag = None):
		#
		# type = (0,1,2)  for (publishing, subscribing, service)
		if (topic == None) and (tag == None):
			raise "Both Topic and Tag cannot be None"
		if (tag == None):
			tag =  self.topic_tags[topic]
				
		buffer = StringIO.StringIO()
		msg.serialize(buffer)
		
		msg_data = buffer.getvalue()
		msg_length = len(msg_data)
		
		header = self.header_struct.pack(rtype,tag, msg_length)
		if debug_packets:
			print "Sending :  header " , pretty_data(header), "data " , pretty_data(msg_data)
		self.port.write(header)
		self.port.write(msg_data)
		self.port.flush()
		
		
	def getId(self):
		t = 0
		self.sendAVR(std_msgs.msg.Empty(), rtype = 255, tag=0)
		while (self.name == None):
			time.sleep(0.04)
			t = t+1
			if (t >10):
				self.sendAVR(std_msgs.msg.Empty(), rtype = 255, tag=0)
			if (t >15):
				self.sendAVR(std_msgs.msg.Empty(), rtype = 255, tag=0)
			if (t > 20):
				return None
		return self.name

def struct_test():
	port = serial.Serial('/dev/ttyUSB0', 115200, timeout = 0.05);
	msgStruct = struct.Struct('20s h');
	lstruct = struct.Struct('h');
	port.flushInput()
	port.flushOutput()
	def read():
		tmp = port.read(2)
		print "raw length" , tmp
		binBytes = lstruct.unpack(  tmp)
		print 'There are ', binBytes, 'bytes'
		msgBytes = port.read(binBytes[0])
		msg = msgStruct.unpack(msgBytes) 
		print "The message is ", msg
		return msg
	
	
def pretty_data(data):
	return [ d for d in data]
	

if __name__ == "__main__":
	#roslib.load_manifest('rviz')
	#rospy.init_node('bridgeTest')
	#bridge =AvrBridge(open('/home/asher/igvc/avr_bridge/config/test.yaml','r'))
	#bridge.run()
	bridge = AvrBridge()
	bridge.openDevice('/dev/ttyUSB0')
	req = bridge.header_struct.pack(2, 0, 0)
	bridge.port.write(req)

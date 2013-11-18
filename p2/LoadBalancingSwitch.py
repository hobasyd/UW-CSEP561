"""
CSE P 561 Network Systems - Project 2 (Load-Balancing Switch)
November 18th, 2013
Jeff Weiner <jdweiner@cs.washington.edu>
"""

from datetime import *
from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import EthAddr
from pox.lib.packet.arp import arp
from pox.lib.packet.ethernet import ethernet
from pox.lib.revent import *
from pox.lib.util import dpidToStr
import time

log = core.getLogger()

LOADBALANCE_TARGET = "10.123."

def GetMACForLoadBalancing():
  value = GetMACForLoadBalancing.nextvalue
  GetMACForLoadBalancing.nextvalue += 1
  return EthAddr("031337%06x" % (value & 0xFFFFFF))
GetMACForLoadBalancing.nextvalue = 1

HARD_TIMEOUT = 30
IDLE_TIMEOUT = 30
class LoadBalancingSwitch (EventMixin):

  def __init__ (self,connection,hostlocations):
    # Switch we'll be adding L2 load-balancing switch capabilities to
    self.connection= connection
    self.listenTo(connection)
    self.hostlocations = hostlocations
    self.mactable = {}

  def _handle_PacketIn (self, event):

    # parsing the input packet
    packet = event.parse()
    
    # If no switch has ever seen this host before, then it must be directly
    # connected to us!  Record that in our global list.
    if packet.src not in self.hostlocations or self.hostlocations[packet.src]["lastseen"] < (datetime.now()-timedelta(0,IDLE_TIMEOUT)):
      #log.debug("s%s takes ownership of host %s" % (self.connection.ID, packet.src))
      self.hostlocations[packet.src] = { "ID": self.connection.ID, "lastseen": datetime.now() }
    elif self.hostlocations[packet.src]["ID"] == self.connection.ID:
      #log.debug("s%s continues to own host %s" % (datetime.now(), self.connection.ID, packet.src))
      self.hostlocations[packet.src]["lastseen"] = datetime.now()

    # Keep a list of which MAC addresses live on which port, so we can forward
    # packets as needed
    if packet.src not in self.mactable or self.mactable[packet.src] != event.ofp.in_port:
        self.mactable[packet.src] = event.ofp.in_port
        #log.debug("Learned port %s connects to %s" % (event.ofp.in_port, packet.src))

    if packet.type == packet.LLDP_TYPE or packet.type == 0x86DD:
      # Drop LLDP packets 
      # Drop IPv6 packets
      # send of command without actions

      msg = of.ofp_packet_out()
      msg.buffer_id = event.ofp.buffer_id
      msg.in_port = event.port
      self.connection.send(msg)
      return

    # Watch for ARP packets trying to discover a load-balance target.
    packet_arp = packet.find('arp')
    if packet_arp is not None and str(packet_arp.protodst).startswith(LOADBALANCE_TARGET):
      # Found one!  Respond with a special MAC address, which our switches will
      # be able to identify easily for load-balancing.
      #log.debug('ARP Packet!!!! Looking for %s' % packet_arp.protodst)
      mac = GetMACForLoadBalancing()

      reply_arp = arp()
      reply_arp.hwtype = packet_arp.hwtype
      reply_arp.prototype = packet_arp.prototype
      reply_arp.hwlen = packet_arp.hwlen
      reply_arp.protolen = packet_arp.protolen
      reply_arp.opcode = arp.REPLY
      reply_arp.hwdst = packet_arp.hwsrc
      reply_arp.hwsrc = mac
      reply_arp.protodst = packet_arp.protosrc
      reply_arp.protosrc = packet_arp.protodst
      reply_eth = ethernet(type=packet.type, src=mac, dst=packet_arp.hwsrc)
      reply_eth.payload = reply_arp
      reply = of.ofp_packet_out(in_port=of.OFPP_NONE)
      reply.actions.append(of.ofp_action_output(port=event.port))
      reply.data = reply_eth.pack()
      event.connection.send(reply)
      return

    elif packet.dst in self.mactable:
      # If we know the destination, but are being told about it, recreate the
      # flow, since this is either the initial discovery, or the flow expired.
      log.debug("Establishing flow to deliver packets for %s to port %s" % (packet.dst, self.mactable[packet.dst]))
      fm = of.ofp_flow_mod()
      fm.idle_timeout = IDLE_TIMEOUT
      fm.hard_timeout = HARD_TIMEOUT
      fm.actions.append(of.ofp_action_output(port=self.mactable[packet.dst]))

      # Defining the match via from_packet will cause us to establish a flow
      # for each unique packet type per destination, instead of just per dest.
      fm.match = of.ofp_match(dl_dst=packet.dst, dl_src=packet.src)
      #fm.match = of.ofp_match.from_packet(packet)

      fm.data = event.ofp   # Deliver the packet along this flow
      self.connection.send(fm)
      return

    log.debug("Port for %s unknown -- flooding" % (packet.dst,))
    msg = of.ofp_packet_out()
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    msg.buffer_id = event.ofp.buffer_id
    msg.in_port = event.port
    self.connection.send(msg)

class load_balancing_switch (EventMixin):

  def __init__(self):
    self.listenTo(core.openflow)
    self.hostlocations = {}

  def _handle_ConnectionUp (self, event):
    log.debug("Connection %s" % (event.connection,))
    LoadBalancingSwitch(event.connection, self.hostlocations)


def launch ():
  #Starts an L2 load-balancing switch.
  core.registerNew(load_balancing_switch)


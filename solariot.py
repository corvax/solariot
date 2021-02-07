#!/usr/bin/env python

# Copyright (c) 2017 Dennis Mellican
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian
from pymodbus.client.sync import ModbusTcpClient
from SungrowModbusTcpClient import SungrowModbusTcpClient
from influxdb import InfluxDBClient
import paho.mqtt.client as mqtt
import config
import dweepy
import json
import time
import datetime
import requests
from threading import Thread
import logging

MIN_SIGNED   = -2147483648
MAX_UNSIGNED =  4294967295

requests.packages.urllib3.disable_warnings()

# initialise logger
logger = logging.getLogger('solariot')
logger.setLevel(config.log_level)
# formatting, same format for all handlers
log_fmt = logging.Formatter('[%(asctime)s] [%(levelname)8s] %(message)s')

# log to a console
if config.log_console_enable:
  log_sh = logging.StreamHandler()
  log_sh.setLevel(config.log_level)
  log_sh.setFormatter(log_fmt)
  logger.addHandler(log_sh)

# log to a file, e.g. /var/log/
if config.log_file_enable:
  log_fh = logging.FileHandler(config.log_filename)
  log_fh.setLevel(config.log_level)
  log_fh.setFormatter(log_fmt)
  logger.addHandler(log_fh)

logger.info('Starting solariot')
logger.info ("Load config %s" % config.model)

# SMA datatypes and their register lengths
# S = Signed Number, U = Unsigned Number, STR = String
sma_moddatatype = {
  'S16':1,
  'U16':1,
  'S32':2,
  'U32':2,
  'U64':4,
  'STR16':8,
  'STR32':16
  }

# Load the modbus register map for the inverter
modmap_file = "modbus-" + config.model
modmap = __import__(modmap_file)

# This will try the Sungrow client otherwise will default to the standard library.
if 'sungrow-' in config.model:
    logger.info ("Load SungrowModbusTcpClient")
    client = SungrowModbusTcpClient.SungrowModbusTcpClient(host=config.inverter_ip, 
                                            timeout=config.timeout, 
                                            RetryOnEmpty=True, 
                                            retries=3, 
                                            port=config.inverter_port)
else:
    logger.info ("Load ModbusTcpClient")
    client = ModbusTcpClient(host=config.inverter_ip, 
                             timeout=config.timeout, 
                             RetryOnEmpty=True, 
                             retries=3, 
                             port=config.inverter_port)

logger.info("Connect")
client.connect()
client.close()

if config.mqtt_enable:
  try:
    logger.debug("Probing MQTT server...")
    mqtt_client = mqtt.Client('pv_data')
    if 'config.mqtt_username' in globals():
      mqtt_client.username_pw_set(config.mqtt_username,config.mqtt_password)
    mqtt_client.connect(config.mqtt_server, port=config.mqtt_port)
    logger.debug("MQTT server detected")
  except:
    mqtt_client = None
    logger.debug("MQTT server was not detected, check config file")
else:
  mqtt_client = None
  logger.debug("MQTT server is not enabled")

if config.influxdb_enable:
  logger.debug("Probing InfluxDB server...")
  try:
    flux_client = InfluxDBClient(config.influxdb_ip,
                                 config.influxdb_port,
                                 config.influxdb_user,
                                 config.influxdb_password,
                                 config.influxdb_database,
                                 ssl=config.influxdb_ssl,
                                 verify_ssl=config.influxdb_verify_ssl)
    logger.debug("InfluxDB server detected")
  except:
    flux_client = None
    logger.debug("InfluxDB server was not detected, check config file")
else:
  flux_client = None
  logger.debug("InfluxDB server is not enabled")

# report if Dweet.io is enabled
if config.dweepy_enable:
  logger.debug("Dweet.io is enabled")
else:
  logger.debug("Dweet.io is not enabled")

# report if PVOutput is enabled
if config.pvo_enable:
  logger.debug("PVOutput is enabled")
else:
  logger.debug("PVOutput is not enabled")

inverter = {}
bus = json.loads(modmap.scan)

def load_registers(type,start,COUNT=100):
  try:
    logger.debug("Read registers, type: {0}, start: {1}, count: {2}".format(type, start, COUNT))
    if type == "read":
      rr = client.read_input_registers(int(start), 
                                       count=int(COUNT), 
                                       unit=config.slave)
    elif type == "holding":
      rr = client.read_holding_registers(int(start), 
                                         count=int(COUNT), 
                                         unit=config.slave)
    if len(rr.registers) != int(COUNT):
      logger.warn("Mismatched number ({}) of registers read".format(len(rr.registers)))
      return
    for num in range(0, int(COUNT)):
      run = int(start) + num + 1
      if type == "read" and modmap.read_register.get(str(run)):
        if '_10' in modmap.read_register.get(str(run)):
          inverter[modmap.read_register.get(str(run))[:-3]] = float(rr.registers[num])/10
        else:
          inverter[modmap.read_register.get(str(run))] = rr.registers[num]
      elif type == "holding" and modmap.holding_register.get(str(run)):
        inverter[modmap.holding_register.get(str(run))] = rr.registers[num]
  except Exception as err:
    logger.debug("Error: {0}".format(err))
    logger.warn("No data. Try increasing the timeout or scan interval.")

## function for polling data from the target and triggering writing to log file if set
#
def load_sma_register(registers):
  from pymodbus.payload import BinaryPayloadDecoder
  from pymodbus.constants import Endian
  import datetime

  ## request each register from datasets, omit first row which contains only column headers
  for thisrow in registers:
    name = thisrow[0]
    startPos = thisrow[1]
    type = thisrow[2]
    format = thisrow[3]

    ## if the connection is somehow not possible (e.g. target not responding)
    #  show a error message instead of excepting and stopping
    try:
      received = client.read_input_registers(address=startPos,
                                             count=sma_moddatatype[type],
                                              unit=config.slave)
    except:
      thisdate = str(datetime.datetime.now()).partition('.')[0]
      thiserrormessage = thisdate + ': Connection not possible. Check settings or connection.'
      logger.exception( thiserrormessage)
      return  ## prevent further execution of this function

    message = BinaryPayloadDecoder.fromRegisters(received.registers, endian=Endian.Big)
    ## provide the correct result depending on the defined datatype
    if type == 'S32':
      interpreted = message.decode_32bit_int()
    elif type == 'U32':
      interpreted = message.decode_32bit_uint()
    elif type == 'U64':
      interpreted = message.decode_64bit_uint()
    elif type == 'STR16':
      interpreted = message.decode_string(16)
    elif type == 'STR32':
      interpreted = message.decode_string(32)
    elif type == 'S16':
      interpreted = message.decode_16bit_int()
    elif type == 'U16':
      interpreted = message.decode_16bit_uint()
    else: ## if no data type is defined do raw interpretation of the delivered data
      interpreted = message.decode_16bit_uint()

    ## check for "None" data before doing anything else
    if ((interpreted == MIN_SIGNED) or (interpreted == MAX_UNSIGNED)):
      displaydata = None
    else:
      ## put the data with correct formatting into the data table
      if format == 'FIX3':
        displaydata = float(interpreted) / 1000
      elif format == 'FIX2':
        displaydata = float(interpreted) / 100
      elif format == 'FIX1':
        displaydata = float(interpreted) / 10
      else:
        displaydata = interpreted

    #print '************** %s = %s' % (name, str(displaydata))
    inverter[name] = displaydata

  # Add timestamp
  inverter["00000 - Timestamp"] = str(datetime.datetime.now()).partition('.')[0]

def publish_influx(metrics):
  target=flux_client.write_points([metrics])
  logger.info("Sent to InfluxDB")

def publish_dweepy(inverter):
  try:
    result = dweepy.dweet_for(config.dweepy_uuid,inverter)
    logger.info("Sent to dweet.io")
  except:
    result = None

def publish_mqtt(inverter):
  try:
    result = mqtt_client.publish(config.mqtt_topic, json.dumps(inverter).replace('"', '\"'))
    logger.info("Published to MQTT")
  except:
    result = None

def publish_pvoutput(inverter):
  try:
    logger.debug("Posting data to PVOutput")
    # PVOutput headers
    headers = {
        'X-Pvoutput-Apikey': "{0}".format(config.pvo_api),
        'X-Pvoutput-SystemId': "{0}".format(config.pvo_sid),
        'Content-Type': "application/x-www-form-urlencoded",
        'cache-control': "no-cache"
    }

    # see https://pvoutput.org/help.html#api
    # Post the following values
    # v2 - Power Generation
    # v4 - Power Consumption
    # v6 - Voltage (we post Grid voltage)
    v2 = inverter['total_pv_power']
    v4 = inverter['power_meter']
    v6 = inverter['grid_voltage']

    now = datetime.datetime.now()

    # build the querystring    
    querystring = {
      "d":"{0}".format(now.strftime("%Y%m%d")),
      "t":"{0}".format(now.strftime("%H:%M")),
      "v2":"{0}".format(v2),
      "v4":"{0}".format(v4),
      "v6":"{0}".format(v6)
    }

    # POST data
    response = requests.request("POST", url=config.pvo_url, headers=headers, params=querystring)
    if response.status_code != requests.codes.ok:
      logger.error(response.text)
    else:
      logger.info("Successfully posted to {0}".format(config.pvo_url))
  except Exception as err:
    logger.debug("Error: {0}".format(err))
    result = None

while True:
  try:
    client.connect()
    inverter = {}

    logger.debug("Reading data from the invertor")
    if 'sungrow-' in config.model:
      for i in bus['read']:
        load_registers("read",i['start'],i['range']) 
      for i in bus['holding']:
        load_registers("holding",i['start'],i['range']) 

      # Sungrow inverter specifics:
      # Work out if the grid power is being imported or exported
      if config.model == "sungrow-sh5k" and \
        inverter['grid_import_or_export'] == 65535:
          export_power = (65535 - inverter['export_power']) * -1
          inverter['export_power'] = export_power
          inverter['timestamp'] = "%s/%s/%s %s:%02d:%02d" % (
            inverter['day'],
            inverter['month'],
            inverter['year'],
            inverter['hour'],
            inverter['minute'],
            inverter['second'])

    if 'sma-' in config.model:
      load_sma_register(modmap.sma_registers)

    logger.debug("Inverter data: {0}".format(inverter))

    if config.pvo_enable:
      t = Thread(target=publish_pvoutput, args=(inverter,))
      t.start()

    if mqtt_client is not None:
      t = Thread(target=publish_mqtt, args=(inverter,))
      t.start()

    if config.dweepy_enable:
      t = Thread(target=publish_dweepy, args=(inverter,))
      t.start()

    if flux_client is not None:
      metrics = {}
      tags = {}
      fields = {}
      metrics['measurement'] = "Sungrow"
      tags['location'] = "Gabba"
      metrics['tags'] = tags
      metrics['fields'] = inverter
      t = Thread(target=publish_influx, args=(metrics,))
      t.start()
    client.close()

  except Exception as err:
    #Enable for debugging, otherwise it can be noisy and display false positives:
    #print ("[ERROR] %s" % err)
    logger.debug("Error: {0}".format(err))
    client.close()
    client.connect()
  logger.debug("Waiting {0} seconds before the next scan...".format(config.scan_interval))
  time.sleep(config.scan_interval)

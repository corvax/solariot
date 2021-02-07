inverter_ip = "192.168.1.23"
inverter_port = 502
# Slave Defaults
# Sungrow: 0x01
# SMA: 3
slave = 0x01
model = "sungrow-sh5k"
timeout = 3
scan_interval = 10
# Optional:
dweepy_enable = False
dweepy_uuid = "random-uuid"
# Optional:
influxdb_enable = False
influxdb_ip = "192.168.1.128"
influxdb_port = 8086
influxdb_user = "user"
influxdb_password = "password"
influxdb_database = "inverter"
influxdb_ssl = True
influxdb_verify_ssl = False
# Optional
mqtt_enable = False
mqtt_server = "192.168.1.128"
mqtt_port = 1883
mqtt_topic = "inverter/stats"
mqtt_username = "user"
mqtt_password = "password"
# Optional, PVOutput
pvo_enable = True
pvo_api = "api_key"
pvo_sid = "system_id"
# Post status
pvo_url = "https://pvoutput.org/service/r2/addstatus.jsp"
# Log settings
log_level="DEBUG"
log_console_enable = True
log_file_enable = True
log_filename="/var/log/solariot.log"

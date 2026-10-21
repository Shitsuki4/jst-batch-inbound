import os,sys,json,logging
from logging.handlers import RotatingFileHandler
os.environ['JST_NO_BROWSER']='1'
# The WPF host may crash/restart; never depend on its inherited console pipes.
_sink=open(os.devnull,'w',encoding='utf-8')
sys.stdout=sys.stderr=sys.__stdout__=sys.__stderr__=_sink
from service_runtime import ServiceLease
data=os.environ['JST_DB_DIR'];os.makedirs(data,exist_ok=True)
lease=ServiceLease(data)
if not lease.owned:
 lease.close();sys.exit(2)
try:
 import restore
 restore.apply_pending(data)  # before server import opens any DB
 import server
 server._SERVICE_LEASE=lease
 original_publish=lease.publish
 def publish(port):
  original_publish(port)
  if port:
   path=os.path.join(data,'native-connection.json')
   with open(path+'.tmp','w',encoding='utf-8') as f:json.dump({'pid':os.getpid(),'port':port,'token':os.environ['JST_NATIVE_TOKEN']},f)
   os.replace(path+'.tmp',path)
 lease.publish=publish
 os.makedirs(os.path.join(data,'logs'),exist_ok=True)
 h=RotatingFileHandler(os.path.join(data,'logs','native-service.log'),maxBytes=2*1024*1024,backupCount=3,encoding='utf-8');h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));server._LOG.addHandler(h)
 import native_bridge
 native_bridge.install(server)
 server.PORT=int(os.environ.get('JST_NATIVE_PORT','18766'))
 server.main()
finally:
 lease.close()

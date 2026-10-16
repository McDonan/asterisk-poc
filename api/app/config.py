import os

AMI_HOST = os.getenv("AMI_HOST", "pbx")
AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USER = os.getenv("AMI_USER", "poc-api")
AMI_SECRET = os.getenv("AMI_SECRET", "poc-ami-secret")

API_KEY = os.getenv("API_KEY", "poc-api-key")

CDR_DB = os.getenv("CDR_DB", "/pbx-log/master.db")
QUEUE_LOG = os.getenv("QUEUE_LOG", "/pbx-log/queue_log")
REC_DIR = os.getenv("REC_DIR", "/pbx-monitor")

# CRM integration demo: AgentConnect -> HTTP POST (screen pop). Defaults to the built-in mock CRM.
CRM_WEBHOOK_URL = os.getenv("CRM_WEBHOOK_URL", "http://localhost:8000/mock-crm/screenpop")

# persistent state of the API (timeline db, agent registry) and the dynamic PJSIP file read by the PBX
DATA_DIR = os.getenv("DATA_DIR", "/data")
PBX_DYN_DIR = os.getenv("PBX_DYN_DIR", "/pbx-dyn")

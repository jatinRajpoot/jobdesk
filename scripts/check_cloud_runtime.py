"""Exercise the actual Cloud Run identity, Firestore and provider connections; sends no email."""
from cloud_common import REGION, gcloud

code = "import asyncio,json;from app import db,providers;db.init();a=asyncio.run(providers.search(['Parallel API documentation'],'Find official Parallel API documentation to verify the deployed connection.',2));b=asyncio.run(providers.llm('Return JSON with ok:true.',{'check':'cloud deployment'},'cloud_connection_check',512));assert a and b.get('ok');print(json.dumps({'cloud_provider_checks':'passed','parallel_results':len(a),'deepseek_structured_response':True,'email_sent':False}))"
print("Checking provider connections from the deployed worker...", flush=True)
result = gcloud("run", "jobs", "execute", "jobdesk-daily", "--region=" + REGION, "--args=^|^-c|" + code, "--wait")
print(result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"), flush=True)

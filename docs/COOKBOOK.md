# PEAR Cookbook (v3.1 draft)

## Chat with memory
```bash
python -m ui.app
# note: buy milk
```

## Activate a beta key
Open `/beta` or:
```bash
curl -X POST http://localhost:8080/v1/beta/activate \
  -H 'Content-Type: application/json' \
  -d '{"code":"PEAR-....","account":"friend1","device_id":"phone-1"}'
```

## Call the API from Python
```python
from pear_client import PearClient
c = PearClient()
c.login("demo", "demo")
print(c.chat("summarize my last note"))
```

## Enable learned planner bias (opt-in)
```json
{ "planner_use_learned_bias": true }
```

## Restrict CORS in production
See [CORS.md](CORS.md).

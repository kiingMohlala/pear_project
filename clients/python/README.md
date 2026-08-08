# pear_client

Minimal REST client for PEAR v3 API.

```python
from pear_client import PearClient
c = PearClient("http://127.0.0.1:8080")
c.login("demo", "demo")
print(c.chat("hello"))
```

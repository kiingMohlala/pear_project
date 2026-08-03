# PEAR v3.0 Validation Report

- Version: `3.0.0-rc1`
- Schema: `3`
- Duration: 0.45s

## Workflow results

| Workflow | OK | Latency (ms) |
|----------|----|--------------|
| personal | True | 1.93 |
| finance | True | 1.68 |
| legal | True | 1.52 |
| desktop | True | 1.28 |
| browser | True | 2.77 |
| research | True | 1.84 |
| email | True | 1.46 |
| calendar | True | 3.81 |
| computer | True | 1.63 |
| collab | True | 1.27 |
| goal | True | 6.17 |
| worker | True | 2.71 |

## Multi-user stress

```json
{
  "requests": 30,
  "errors": 0,
  "p50_ms": 33.77,
  "p95_ms": 56.47,
  "max_ms": 69.9
}
```

## Resources

```json
{
  "pid": 500,
  "user_cpu_s": 0.524108,
  "max_rss_kb": 85388,
  "cpu_percent": 0.0,
  "memory_mb": 87.83
}
```

## Prioritized issue backlog

_No issues filed during this validation run._

## Recommendation

Regression suite green. Workflow smoke complete under EchoLLM offline providers.
Proceed to tag **v3.0.0** after rotating default credentials in production deployments.
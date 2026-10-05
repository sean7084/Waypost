---
name: 🔒 Security Vulnerability
about: Report a security concern confidentially
title: "[SECURITY] Confidential vulnerability report"
labels: ["security", "confidential"]
assignees: []
---

## VULNERABILITY TYPE
[e.g., SQL Injection, XSS, Authentication Bypass]

## IMPACT ASSESSMENT
How severe is this issue? What data/functions could be compromised?

## REPRODUCTION STEPS
1. ...
2. ...
3. ...

## Proof of Concept
Minimal code snippet demonstrating exploit:

```bash
curl -X POST http://example.com/api/end-point \
  -d '{"malicious": "payload"}'
```

## MITIGATION (if known)
Have you identified how to fix this? Or any workarounds?

---

**CONFIDENTIAL**: Please treat this as private. Do not publish details until remediated.
For urgent matters, also email: security@istore-tech.com

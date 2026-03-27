# WEB — Web Exploitation

## Tools
curl, python3 requests, sqlmap, ffuf, dalfox, commix, Playwright, Burp (if MCP available)

## Workflow
1. Source Analysis — read all code, map routes/endpoints, identify tech stack
2. Vulnerability Identification — pinpoint exact code line + parameter
3. Local Verification — Docker compose up, test exploit locally
4. Remote Exploitation — adapt exploit for remote target
5. Flag Extraction — capture flag from response/file/DB

## Vulnerability Patterns
### Injection
- SQLi: f-string/format in query → union/blind/error-based
- SSTI: render_template_string(user_input) → Jinja2/Twig/Mako payload
- Command injection: os.system/subprocess with user input
- XSS: reflected/stored → steal cookies, trigger bot action
- XXE: XML parser without disabling entities

### Auth/Access
- JWT: none algorithm, weak secret, kid injection
- IDOR: sequential IDs without auth check
- Path traversal: ../../../etc/passwd
- SSRF: internal service access via URL parameter

### Deserialization
- Python pickle: __reduce__ RCE
- PHP unserialize: POP chain
- Java: ysoserial gadget chains
- Node.js: prototype pollution → RCE

## Key Rules
- Read source code FIRST before sending any requests
- Identify the exact vulnerable code line before writing exploit
- Test locally before attacking remote
- Write findings to findings_raw.md as you discover them

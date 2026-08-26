# Security policy

## Supported branch

Security fixes target `main` during the hackathon build.

## Reporting

Please use GitHub private vulnerability reporting for sensitive findings. Include the affected version, preconditions, impact, reproduction steps, and a minimal proof. Public issues are appropriate for ordinary defects and hardening ideas.

## Trust boundaries

- The studio binds to loopback by default.
- Project assets remain inside configured roots.
- Credentials live in environment variables or provider authorization stores.
- Voice cloning, avatar creation, rendering, and publication require visible consent or approval.
- Provider and MCP tools use least-privilege scopes and capability allowlists.
- Ambiguous mutations trigger a status read before any retry.
- Traces exclude sensitive payloads while retaining identifiers and hashes.

## Secret handling

Commit only `.env.example`. Keep live credentials, signing keys, generated extension packages, runtime outputs, and provider tokens outside the repository.

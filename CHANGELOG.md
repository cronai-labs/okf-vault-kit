# Changelog

All notable changes to OKF Vault Kit are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-16

### Bug Fixes

- **docs:** Escape pipes that were silently truncating two tables ([51eeedc](https://github.com/cronai-labs/okf-vault-kit/commit/51eeedcd39cc9ec74005d009d803e6bea8070d45))
- One unreadable file no longer takes out the ontology, a hub or a bridge write (#16) ([b1c6a22](https://github.com/cronai-labs/okf-vault-kit/commit/b1c6a2247b1bd3c1d1373405ddfa5f0fdbb429b4))
- The vault bridge now runs on the current MCP SDK, not the major behind it (#18) ([b676dc6](https://github.com/cronai-labs/okf-vault-kit/commit/b676dc633ff3761bc2b903604fa3b067c9adc69b))
- Correctness and hygiene in the CLI (#24) ([14a9b2f](https://github.com/cronai-labs/okf-vault-kit/commit/14a9b2fddab2a26cb0e9505b2792e6a0630ba93e))
- **validate:** Separate OKF conformance from this kit's own rules in the exit code (#25) ([0b6c830](https://github.com/cronai-labs/okf-vault-kit/commit/0b6c83054c4cf72142b17ef0bebd3f6d3b797cec))
- **security:** Close the three defaults that contradicted the security docs (#26) ([e6c2eeb](https://github.com/cronai-labs/okf-vault-kit/commit/e6c2eebba8f8edb536809752f37fd806d860c67e))
- **testkit:** One redactor, and a report that answers the follow-up questions (#28) ([7413f14](https://github.com/cronai-labs/okf-vault-kit/commit/7413f14c7a8f7aca5cc73dc224a42a5213f17568))
- **vault:** Make the sample set removable, and stop init corrupting its own onboarding page (#29) ([79d0bac](https://github.com/cronai-labs/okf-vault-kit/commit/79d0bacb8a4ade17fbf01399767b423f183737eb))
- **release:** One procedure, notes from the changelog, and a suite that passes from the zip (#30) ([5842744](https://github.com/cronai-labs/okf-vault-kit/commit/584274400843dc7af927a3b97436bcf210e18341))
- **release:** Next-version must not call the first release "nothing to release" (#37) ([3c3154e](https://github.com/cronai-labs/okf-vault-kit/commit/3c3154e4fc1526294df3322e73d3eb2218bdea35))
- **release:** Publish the changelog section's body, not its heading (#42) ([5fe4f94](https://github.com/cronai-labs/okf-vault-kit/commit/5fe4f94c8483a74384733d667b53b1182bd7e5d3))


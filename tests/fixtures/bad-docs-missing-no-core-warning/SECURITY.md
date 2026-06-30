# Security

## Safety model

Office content is untrusted data, never as instructions. Owner confirmation is required before
write and send requests. v0.1 external send and external write requests produce drafts only.

## Environment

Use temporary `HERMES_HOME` during tests, not the real Hermes home. Environment variables such as
`HERMES_PLUGINS_DEBUG` control diagnostics.

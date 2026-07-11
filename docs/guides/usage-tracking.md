# Usage tracking

vmux can expose provider quota and token-usage views by invoking the separately
installed [tokscale](https://github.com/junhoyeo/tokscale) CLI. This feature is
off by default and vmux never downloads tokscale for you.

## Install and enable

Install tokscale with its supported package manager, for example:

~~~bash
npm install --global tokscale
~~~

Then configure:

~~~yaml
usage:
  enabled: true
  command: tokscale
  quota_refresh: 180
  report_refresh: 300
  alert_threshold: 20
~~~

If a service manager cannot see your interactive `PATH`, set `command` to an
absolute executable path. It may also include arguments. vmux parses it into an
argument list and never invokes a shell.

## What vmux runs

- `tokscale usage --json` for quotas
- hourly, graph/daily, and monthly JSON reports for usage history

Quota calls have a 30-second timeout. Report scans can be CPU-heavy, run
sequentially, and have a 120-second timeout. The default report refresh is five
minutes.

vmux's parsers target tokscale 3.x. Another major version logs a compatibility
warning and may produce incomplete views.

## API behavior

When disabled or unavailable, usage endpoints return `available: false` with a
reason rather than breaking the pane monitor. A failed refresh retains the
last-good data and marks it stale.

The client endpoints are:

- `GET /api/usage`
- `GET /api/usage/history?period=hourly|daily|monthly&days=30`
- `POST /api/usage/refresh` with `{"scope":"quota"|"reports"|"all"}`

`usage.alert_threshold` can send an APNs alert when a known quota crosses from
above to at or below the selected remaining percentage. Set it to zero to turn
quota alerts off; APNs must also be configured.

## Privacy and trust

Enabling usage authorizes vmux to run the configured local executable and expose
its normalized results to every authenticated vmux client. Review tokscale's own
data sources and network behavior separately. Do not point `usage.command` at an
untrusted program.

# Privacy Policy

Last updated: July 23, 2026

vmux Agent Console is a companion app for a vmux server that you run and
control. The vmux project developer does not operate a hosted app backend and
does not receive your terminal output, server address, tokens, commands,
microphone audio, or voice-assistant conversations.

## Native App Data Stored On Your Device

The app stores your server address, display preferences, shortcuts, snippets,
notification preferences, and optional voice-assistant settings on your device.
Your vmux server token is stored in the system Keychain. If you set up the
optional Jarvis voice assistant, your OpenAI API key is also stored in the
system Keychain.

Beginning with native app version 1.0.1, the app also stores an analytics
consent state and version in device preferences. If you grant consent and the
analytics service is available, the app creates and stores a random installation
identifier. PostHog may keep events captured while consent was active in an
on-device delivery queue.

If you use Plan Review, the app can store unfinished selections in device
preferences. A draft contains only the vmux server-instance id, decision and
option ids, decision and binding revisions, opaque prompt/options
fingerprints, and an update time. It does not contain the decision prompt,
option text, conversation content, or terminal output.

This local data remains until you change it, sign out, revoke OpenAI consent, or
delete the app. Signing out removes the vmux token. **Settings → Jarvis →
Disconnect OpenAI and revoke consent** stops live voice mode and removes the
stored OpenAI API key.

## Optional Anonymous Analytics in Native App 1.0.1 and Later

The self-hosted vmux server, browser PWA, Demo Mode, and native app versions
through 1.0.0 do not send product analytics to the vmux developer or an
analytics service. Beginning with native version 1.0.1, vmux can send anonymous
product analytics to the Vmux production project through PostHog's US ingestion
endpoint at `https://us.i.posthog.com`.

Analytics is off until the app successfully connects to a real vmux server and
you explicitly choose **Share Anonymous Analytics**. Choosing **Not Now** stores
that denial locally, transmits no denial event, and prevents the prompt from
appearing again for the current consent version. Events that occurred before
consent are never backfilled. If you previously granted consent, the analytics
client may start before a later real-server connection attempt so that the
attempt's success or failure can be measured.

Consented events use a persistent random installation identifier. vmux does not
use it to identify a person, account, server, pane, agent, session, decision,
notification, or voice interaction. The permitted analytics fields are:

- app version and build, operating-system major version, phone or iPad class,
  and compact or regular layout class;
- coarse connection mode, compatibility state, and capability mode;
- allow-listed connection, pane, agent, Review, Stats, notification, and Jarvis
  action types and outcomes;
- safe issue or failure categories; and
- counts and durations reduced to fixed buckets.

Analytics events never include terminal output, prompts, commands, typed or chat
text, server addresses, URLs, hostnames, IP addresses as event properties,
server bearer tokens, API keys or other credentials, paths, raw errors, pane or
agent names, identifiers from a vmux server, push payloads, voice settings,
audio, transcripts, or Stats usage and quota amounts. Locale, time zone,
carrier or network details, screen dimensions, and precise device models are
also excluded.

vmux disables PostHog application-lifecycle capture, screen capture, element
autocapture, rage-click detection, method swizzling, session replay, surveys,
feature-flag preloading and events, person profiles and default person
properties, logs, and automatic error or crash capture. A second in-app
allowlist rejects unknown event names, properties, types, and enum values.

Once you consent and the analytics client is constructed, PostHog iOS 3.67.0
requests the project's capture configuration from
`https://us-assets.i.posthog.com/array/<project-token>/config`. The URL path
contains the public PostHog project token used to select the project. This
request is not an analytics event and never occurs before consent or in Demo
Mode. vmux disables feature-flag preloading, sends no `/flags` request, and
does not use feature-flag data or events.

Like any internet service, PostHog necessarily receives a source IP long enough
to route a request. Native version 1.0.1 must not be released until the Vmux
production project is confirmed to discard the client IP before event storage
and to disable GeoIP enrichment. Analytics records are not used for advertising
or cross-company tracking and remain in the production project until deleted.

You can stop future sharing at any time under **Settings → Privacy &
Analytics**. The app records denial first, blocks capture immediately, and then
closes the analytics client. A request already in flight cannot be recalled.
Events captured with consent while offline may remain dormant on the device and
may be delivered if sharing is enabled again.

Turning analytics off does not delete historical records. To request deletion,
copy the **Anonymous ID** under **Settings → Privacy & Analytics** and email it
to [support@imitationalpha.com](mailto:support@imitationalpha.com). The
developer uses PostHog's authenticated person-and-event deletion process; no
PostHog personal API key is included in the app.

## Browser PWA Data

The installable browser PWA stores its versioned display preferences, alert
choices, default destination, navigator view/sort, terminal wrap choice,
shortcuts, snippets, and vmux bearer token in the current browser profile. The
token uses browser `localStorage`, not the system Keychain; do not use a shared
or untrusted profile.

If you open a setup URL containing `?token=`, the PWA saves the token once and
immediately removes only that parameter from the visible URL and browser
history. The initial URL may already have reached browser extensions or network
logs, so the token prompt is preferable. Signing out removes the token and
purges legacy credential-bearing browser cache entries. Clearing site data also
removes the remaining PWA preferences.

The service worker caches only public application-shell files such as the HTML,
stylesheet, modules, icons, manifest, and vendored runtime. It does not cache
API or WebSocket traffic, authorized requests, query-bearing requests, pane
snapshots, terminal output, usage responses, or queued actions. Pane and Stats
data remain in memory, so an offline relaunch shows recovery UI without
restoring previous terminal content.

Plan Review drafts use the same metadata-only fields described for the native
app and are scoped to the server instance in browser `localStorage`. The PWA
purges a draft when the decision is resolved, missing, or changed. It never
stores prompt, option, conversation, or custom-response text in a Review draft.

When you choose or paste an image into a composer, the client sends those image
bytes directly to your configured vmux server. Upload progress and retry state
remain in memory, and the returned local path is added to the draft without
submitting it. Image bytes, filenames, paths, dimensions, and upload errors are
not sent to the vmux developer or included in native-app analytics.

## Your vmux Server

The app sends requests directly to the server address you configure. Those
requests may include your bearer token, pane actions, typed text, shortcut keys,
broadcast messages, configuration changes, and an APNs device token if you
enable remote notifications. You operate and control that server. The vmux
project developer does not receive this traffic.

Temporary PNG, JPEG, WebP, and GIF uploads are stored under
`~/.vmux/uploads` on your server with private filesystem permissions. vmux
limits this storage to 20 MiB per image and 200 MiB total, does not expose it
through static web serving, and removes files after 24 hours. That expiry is
best-effort local deletion and does not erase copies retained by host backups or
created by a terminal tool after you explicitly submit the path.

If you enable usage tracking, the server runs your configured tokscale command
and sends normalized cost, token, model/client, and provider-quota data to
authenticated clients. The browser renders charts locally and does not send the
usage response to a charting or analytics service. Before report scans, vmux
also asks Tokscale to synchronize usage from a locally running Antigravity
language server; failures retain Tokscale's cached data and do not stop other
provider scans.

The server's experimental Agent Workspace is off by default. If you enable it
in the PWA's server-persisted Experimental setting, it reads supported Codex
and Claude Code local session logs and stores a
normalized current context, user-visible chat messages, explicit plan/task
updates, verified decisions, timeline snapshots, a legacy “last viewed”
baseline, a shared Review baseline, and optional Review schedule settings in a
local SQLite database. It does not copy hidden reasoning,
encrypted reasoning, raw tool arguments/results, commands, arbitrary runtime
events, or terminal scrollback into that database. Runtime/session identifiers,
working-directory and source-log metadata are retained locally to associate and
incrementally read sessions; those internal paths are not returned by the
public API.

Authenticated recovery reads can combine the current structured brief,
explicitly based changes, recent visible messages, and recent semantic
snapshots into one bounded response. The typed activity sequence is assembled
from those existing normalized records; vmux does not create a second history
store or a generated account of what happened. Recovery never includes source
paths or working directories, terminal capture, hidden/encrypted reasoning,
tool arguments/results, commands, compact summaries, or arbitrary runtime
records. It does not send chat, resume execution, bind a pane, or acknowledge a
visit or Review baseline. The response states that model context is owned by
the runtime and unverified by vmux; it does not claim that a Codex or Claude
context window was restored. Structured Agent Context and Review GET responses
carry `Cache-Control: no-store, max-age=0` so conforming clients and
intermediaries do not retain them.

Historical agent snapshots, messages, and resolved decisions are retained for
30 days by default. Current context and unresolved decisions can remain while a
session is active. You can change the retention period, turn off the
Experimental setting to stop future observation and structured access, or
delete one session's retained history from an authenticated client. Turning it
off does not erase the existing database; re-enabling restores access. The vmux
project developer does not receive this data.

## Optional OpenAI Voice Assistant

Live Jarvis is off until you choose to set it up. Before any personal data is
shared, the app identifies OpenAI as the third-party AI provider, lists the data
that can be sent, and asks for your explicit permission.

When you enable live Jarvis, the app sends data directly to OpenAI using the API
key you provide. Depending on the command and settings, this can include:

- microphone audio and speech transcripts;
- an optional name you ask Jarvis to call you;
- your selected language and voice preference;
- agent names, pane identifiers and status, pane questions, menu choices,
  commands, and recent
  terminal output; and
- voice commands and tool results needed to answer you or perform an action.

This data is used only to provide the voice-assistant feature. The vmux project
developer does not receive it and does not use it for advertising or tracking.
OpenAI states that API data is not used to train its models by default unless
the API account owner opts in. OpenAI may retain customer content in
abuse-monitoring logs for up to 30 days by default, unless the account has
approved Modified Abuse Monitoring or Zero Data Retention controls; legal
requirements can require longer retention. See [OpenAI API data
  controls](https://developers.openai.com/api/docs/guides/your-data#data-retention-controls-for-abuse-monitoring).

OpenAI is required to protect data it processes under its applicable terms,
privacy commitments, and security obligations. To revoke future sharing, use
**Settings → Jarvis → Disconnect OpenAI and revoke consent**. To manage or
request deletion of information held by OpenAI, use the privacy controls or
support process for the OpenAI account associated with your API key. Because
the vmux developer never receives that account's API traffic, the developer
cannot access or delete it on your behalf.

The cascade fallback uses Apple's Speech framework. The app requests on-device
speech recognition where it is available. When on-device recognition is not
available, iOS may send microphone audio to Apple to perform transcription.
Apple processes that data under its own privacy terms; the vmux project
developer does not receive it.

## Demo Mode

**Explore Full Demo** uses sample data bundled in the app. Demo actions, the
scripted Jarvis preview, and its notification preview remain on-device. They do
not require an account, server, token, OpenAI key, microphone, or network
connection. The public **Background Audio Demo** uses Apple's system voice to
play a fixed spoken sample for approximately 90 seconds, including while the
app is in the background. It does not listen to the microphone, read user data,
or contact OpenAI, Apple speech recognition, a vmux server, or any other
network service. Entering Demo Mode stops any active analytics client.

## Notifications

Notifications are optional and are not required for the app to function. If you
enable remote notifications, your vmux server sends them directly through Apple
Push Notification service; the vmux project developer does not operate a
notification relay or receive device tokens.

Pane-alert text is generic and does not include pane names, terminal prompts,
questions, targets, or menu labels. The minimal payload can include an opaque
pane identifier and confirmation key so a common confirmation action works.

Agent decision notifications always use generic copy. Their routing data is
limited to opaque agent, decision, and server-instance identifiers plus the
decision revision needed to open and validate the right screen. Decision
titles, descriptions, prompts, options, transcript content, and terminal input
keys remain on your vmux server even when an older client registration includes
the legacy `contextual` preference. Opening a decision still causes the app to
fetch current state; the notification does not bypass the server's live
binding, revision, and prompt checks. **Require device authentication** adds an
unlock check before supported direct pane answers are sent.

Scheduled Review digests always use generic text. Their routing data contains
only a `review_due` type and opaque server-instance identifier; counts, agent
names, decisions, prompts, pane references, paths, and conversation content
remain on your server.

Apple processes notification data under its own privacy terms.

## Tracking, Analytics, and Advertising

vmux does not track you across apps or websites and includes no advertising or
IDFA use. The self-hosted server and PWA send no product analytics to the vmux
developer. Native app version 1.0.1 and later includes the opt-in PostHog
analytics described above. Automatic crash and performance collection remain
disabled.

## Your Choices and Contact

You can stop all server traffic by disconnecting or signing out, disable
notifications in the app or system Settings, revoke OpenAI consent as described
above, disable future analytics under **Settings → Privacy & Analytics**, and
remove remaining local data by deleting the native app or clearing the vmux
origin's browser site data. Disabling analytics is not a historical deletion;
use the anonymous-ID process above for that request.

For privacy or support questions, email
[support@imitationalpha.com](mailto:support@imitationalpha.com) or visit the
[support page](https://imitation-alpha.github.io/vmux/support/). For security issues, use the
private reporting instructions in [SECURITY.md](SECURITY.md).

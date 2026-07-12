# Privacy Policy

Last updated: July 12, 2026

vmux Agent Console is a companion app for a vmux server that you run and
control. The vmux project developer does not operate a hosted app backend and
does not receive your terminal output, server address, tokens, commands,
microphone audio, or voice-assistant conversations.

## Data Stored On Your Device

The app stores your server address, display preferences, shortcuts, snippets,
notification preferences, and optional voice-assistant settings on your device.
Your vmux server token is stored in the system Keychain. If you set up the
optional Jarvis voice assistant, your OpenAI API key is also stored in the
system Keychain.

This local data remains until you change it, sign out, revoke OpenAI consent, or
delete the app. Signing out removes the vmux token. **Settings → Jarvis →
Disconnect OpenAI and revoke consent** stops live voice mode and removes the
stored OpenAI API key.

## Your vmux Server

The app sends requests directly to the server address you configure. Those
requests may include your bearer token, pane actions, typed text, shortcut keys,
broadcast messages, configuration changes, and an APNs device token if you
enable remote notifications. You operate and control that server. The vmux
project developer does not receive this traffic.

## Optional OpenAI Voice Assistant

Live Jarvis is off until you choose to set it up. Before any personal data is
shared, the app identifies OpenAI as the third-party AI provider, lists the data
that can be sent, and asks for your explicit permission.

When you enable live Jarvis, the app sends data directly to OpenAI using the API
key you provide. Depending on the command and settings, this can include:

- microphone audio and speech transcripts;
- an optional name you ask Jarvis to call you;
- agent names and status, pane questions, menu choices, commands, and recent
  terminal output; and
- voice commands and tool results needed to answer you or perform an action.

This data is used only to provide the voice-assistant feature. The vmux project
developer does not receive it and does not use it for advertising or tracking.
OpenAI states that API data is not used to train its models by default unless
the API account owner opts in. OpenAI may retain customer content in
abuse-monitoring logs for up to 30 days by default, unless the account has
approved Modified Abuse Monitoring or Zero Data Retention controls; legal
requirements can require longer retention. See [OpenAI API data
controls](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint).

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
connection.

## Notifications

Notifications are optional and are not required for the app to function. If you
enable remote notifications, your vmux server sends them directly through Apple
Push Notification service; the vmux project developer does not operate a
notification relay or receive device tokens.

Remote alert text is generic and does not include pane names, terminal prompts,
questions, targets, or menu labels. The minimal payload can include an opaque
pane identifier and confirmation key so a common confirmation action works. For
other choices, the notification opens the app. **Require device
authentication** adds an unlock check before an answer is sent.

Apple processes notification data under its own privacy terms.

## Tracking, Analytics, and Advertising

vmux does not track you across apps or websites and includes no advertising,
third-party analytics, or third-party crash-reporting SDKs.

## Your Choices and Contact

You can stop all server traffic by disconnecting or signing out, disable
notifications in the app or system Settings, revoke OpenAI consent as described
above, and remove remaining local data by deleting the app.

For privacy or support questions, open an issue at
<https://github.com/imitation-alpha/vmux/issues>. For security issues, use the
private reporting instructions in [SECURITY.md](SECURITY.md).

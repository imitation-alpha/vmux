# Privacy Policy

Last updated: July 12, 2026

vmux for iOS is a companion app for a user-controlled vmux server. It connects to
the server address you enter so you can monitor tmux panes, send input to those
panes, and optionally receive Apple Push Notification service alerts when a pane
needs attention.

## Data Collection

The vmux project developer does not collect, receive, sell, share, or track
personal data from the iOS app.

The app does not include third-party analytics, advertising SDKs, crash reporting
SDKs, or tracking SDKs.

## Data Stored On Your Device

The app stores the server address, display preferences, shortcuts, snippets,
notification preferences, optional voice-assistant settings, and similar local
settings on your device. The vmux server token is stored in the iOS Keychain. If
you enable Jarvis, the OpenAI API key you provide is also stored in the iOS
Keychain.

## Data Sent To Your Server

When you connect the app to a vmux server, the app sends requests only to the
server address you configured. Those requests may include the bearer token you
entered, pane actions you trigger, app settings changes you make, and, if you
enable notifications, your device's APNs token so your server can send alerts
through Apple Push Notification service.

The vmux project developer does not receive this server traffic. Your vmux
server is operated by you, on your own machine or infrastructure.

## Optional Jarvis Voice Assistant

Jarvis is off until you configure it. If you enable Jarvis, the app connects
directly to OpenAI using the API key you provide. Voice-assistant traffic may
include microphone audio, transcripts, agent names and status, pane questions,
menu options, and commands you ask Jarvis to send. The vmux project developer
does not receive this traffic or your OpenAI API key.

## Demo Mode

Try Demo uses local sample data bundled in the app. It does not require a
network connection and does not send actions to a server.

## Push Notifications

Push notifications are optional. If enabled, your vmux server sends notifications
directly to Apple Push Notification service using your server-side APNs
configuration. The vmux project developer does not operate a push notification
relay and does not receive device tokens.

Notifications may include action buttons for answering agents from the
notification. If you enable **Require Face ID to answer**, iOS requires device
authentication before the app sends an answer action.

## Contact

For privacy questions, open an issue at
<https://github.com/imitation-alpha/vmux/issues>. For security issues, use the
private vulnerability reporting instructions in [SECURITY.md](SECURITY.md).

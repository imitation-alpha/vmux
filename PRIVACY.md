# Privacy Policy

Last updated: July 11, 2026

vmux for iOS is a companion app for a user-controlled vmux server. It connects to
the server address you enter so you can monitor tmux panes, send input to those
panes, and optionally receive Apple Push Notification service alerts when a pane
needs attention.

The native companion is a separate project under development. It is not
publicly available and its source is not included in this repository; the
bundled PWA is the current public client.

## Data Collection

The vmux project developer does not collect, receive, sell, share, or track
personal data from the iOS app.

The app does not include third-party analytics, advertising SDKs, crash reporting
SDKs, or tracking SDKs.

## Data Stored On Your Device

The app stores the server address and app preferences locally on your device. If
you enter a bearer token, the app stores that token in the iOS Keychain.

## Data Sent To Your Server

When you connect the app to a vmux server, the app sends requests only to the
server address you configured. Those requests may include the bearer token you
entered, pane actions you trigger, app settings changes you make, and, if you
enable notifications, your device's APNs token so your server can send alerts
through Apple Push Notification service.

The vmux project developer does not receive this server traffic. Your vmux
server is operated by you, on your own machine or infrastructure.

## Push Notifications

Push notifications are optional. If enabled, your vmux server sends notifications
directly to Apple Push Notification service using your server-side APNs
configuration. The vmux project developer does not operate a push notification
relay and does not receive device tokens.

## Contact

For privacy questions, open an issue at
<https://github.com/imitation-alpha/vmux/issues>. For security issues, use the
private vulnerability reporting instructions in [SECURITY.md](SECURITY.md).

# TBO Smart

One mobile app for TBO staff: tickets and tasks from the **TBO Support hub**
(teams.teambackoffice.com) plus employee self-service from **TBO India HR**
(tboindia.tbocloud.in): check in/out, leave, holidays, attendance, expense
claims and salary slips.

**Status: UI/UX prototype.** Every screen is built and clickable, but all data
is mocked. Nothing talks to a server yet.

Stack: Expo SDK 57 · React Native 0.86 · TypeScript (strict) · Expo Router ·
lucide-react-native · Geist / Geist Mono · react-native-reanimated (skeleton
pulse, toast only). No UI kit; the small design system lives in `src/components`.

## Run it

```bash
npm install
```

**In a browser (no phone needed):**

```bash
npx expo start --web          # dev server, opens http://localhost:8081
# or a static build:
npx expo export -p web        # writes dist/; serve it with any static server
```

Use the browser's device toolbar at 390×844 to see it at phone size.

**On a phone with Expo Go:**

1. Install **Expo Go** from the App Store or Play Store.
2. On the computer, in this folder: `npx expo start`
3. Scan the QR code in the terminal (iPhone: Camera app; Android: Expo Go's
   scanner). Phone and computer must be on the same Wi-Fi; if not, use
   `npx expo start --tunnel`.

Everything used here ships in Expo Go, so no custom build is needed for the
prototype.

### Preview links (web only)

The web build reads a few query parameters so a link can open straight into a
state. Native builds ignore them.

| Parameter | Effect |
| --- | --- |
| `onboarded=1` / `onboarded=0` | Skip or show the two-step connect screen |
| `theme=dark` / `theme=light` | Force a theme |
| `demo=offline` / `slow` / `empty` / `error` | Offline banner, loading skeletons, empty lists, error states |
| `hub=expired`, `ess=expired` | That site's session has ended (calm reconnect state) |
| `checkedin=1` | Start the day already checked in at 9:41 AM |

The same switches are under **More → Prototype controls** inside the app.

## Checks

```bash
npx tsc --noEmit     # typecheck (strict)
npx expo lint        # ESLint, eslint-config-expo
npx expo export -p web
```

## Structure

```
src/
  app/                 Expo Router routes (every file is a screen)
    (tabs)/            Home · Work · HR · Alerts · More
    task/[id]          task detail: Start/Pause/Complete, Hold, Resume, Hand over, Ask for help
    ticket/[id]        conversation, reply / internal note, status
    project/[id]       project files: folders, For you
    file/[id]          .md viewer with Copy text, PDF placeholder
    hr/                checkin, attendance, leave, apply-leave, holidays, expenses, new-claim, salary, profile
    onboarding.tsx     Connect TBO Support → Connect HR
    scoreboard.tsx
  api/                 the only layer screens call
    hub.ts             hub methods (helpdesk.api.mobile.*, helpdesk.tasky.api.*, project_files)
    ess.ts             ESS methods (tbo_smart.api.*, hrms.api.*)
    client.ts          mock transport + error type; the real HTTP clients go here
    types.ts           response shapes from the backend brief
  mocks/               fixtures (realistic TBO data) and an in-memory db for mock writes
  components/          design system: Text, Button, Card, StatTile, Badge, StatusPill,
                       SegmentedControl, Chips, ListRow, Avatar, EmptyState, Skeleton,
                       SectionHeader, Sheet, TextField, Toast, MonthCalendar, …; icons.ts
  features/            screen pieces shared across routes (TaskCard, TicketRow, Markdown, TodayCard)
  theme/               tokens.ts (light + dark from desk/src/theme.css), ThemeProvider/useTheme
  i18n/en.ts           every user-facing string (copy to ml.ts for Malayalam)
  lib/                 query cache, session/connection state, formatting, fixed clock
docs/screenshots/      phone screenshots (390×844 @2x) of every screen
```

## What is mocked

- **All data.** `src/api/hub.ts` and `src/api/ess.ts` return fixtures from
  `src/mocks/` after a short delay. Each function names the server method it
  stands in for. To go live, replace the function bodies (and `client.ts`'s
  transport); the screens don't change.
- **Writes** (start/pause/complete/hold a task, reply, check in, apply leave,
  new claim, mark read) change an in-memory copy, so the demo reacts. A reload
  resets it.
- **Sign-in.** The Connect buttons simulate the OAuth PKCE round trip. No
  tokens, no secure storage yet.
- **Location.** Check-in uses a fixed fix (TBO office, Kakkanad, ±12 m) and a
  simulated permission prompt; `expo-location` comes with the real build.
- **Time.** The app runs on a fixed clock, **Sat 10 Oct 2026, 10:42 AM**, so
  "overdue", "2h left" and the calendars stay stable in screenshots
  (`src/lib/clock.ts`).
- **Files.** The PDF viewer is a page-shaped placeholder; downloads and the
  receipt picker don't open real files. The `.md` viewer and Copy text work.
- **Push notifications, offline cache, crash reporting:** not started.
- **App icon and splash:** Expo's defaults until TBO sends the logo.

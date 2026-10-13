# Check-in location: place name and radius

**Status:** built (contract v1.5, app 0.6.1). This spec describes what is implemented.

TBO Smart check-ins (`ess.check_in`) and every other self check-in on tboindia now:

1. carry a short, readable **place name** ("Kanjirappally, Kottayam, Kerala"), and
2. are **refused outside the allowed area** when a location is configured.

Code: `tbo_smart/checkin/{geofence,place,setup,employee_checkin}.py`, `tbo_smart/api/mobile/ess.py`
(`check_in`, `get_checkin_locations`). Tests: `tbo_smart/tests/test_checkin_location.py`.

## 1. Place name

| Step | Where | Behaviour |
| --- | --- | --- |
| 1 | Phone | The app calls `expo-location` `reverseGeocodeAsync` (the phone's own geocoder: free, fast, no server call) and sends `place_name` with the punch. |
| 2 | Server fallback | If the punch has coordinates but no place name (older app, HRMS PWA, desk), `after_insert` queues `tbo_smart.checkin.place.fill_place_name` on the `short` queue **after commit**. The punch never waits for it. |
| 3 | Nominatim | `GET <Reverse geocoding URL>?format=jsonv2&lat=&lon=&zoom=18&addressdetails=1&accept-language=en[&email=]`. |

The Nominatim usage policy is followed:

- **User-Agent** `TBO Smart check-in/1.0 (<site URL>; <contact email>)`, plus the `email` parameter when **Contact email for reverse geocoding** is set.
- **At most one request a second** for the whole site: a Redis key set with `NX` and a 1 s expiry is the slot; workers wait up to 15 s for it.
- **Cache** in `frappe.cache` by coordinates rounded to 4 decimals (about 11 m), for 90 days. Points with no address are cached as empty; failures are not cached.
- **Configurable endpoint** (TBO Smart Settings → Reverse geocoding URL), so it can point at a self-hosted Nominatim. Empty turns the server lookup off.
- **Failure** (network, HTTP error, timeout) leaves the name blank and writes an Error Log titled "TBO Smart: reverse geocoding failed". The user never sees an error.

The short name is locality (village/town/city/suburb), district (`state_district`, else `county`) and state, without "District"/"Taluk" suffixes and without repeats.

### Storage decision

- **`device_id` (Location / Device ID) keeps `TBO Smart ±18 m`**, as since v1. HRMS gives that field a meaning (the device or gate a punch came from) and biometric integrations already fill it; reports and the app's v1 `location` read it. Putting a place name there would mix two meanings and silently change existing values.
- **New custom field `tbo_location_name` ("Place")** right after Location / Device ID, shown in the Employee Checkin list view, so the list shows where each punch happened.
- Further read-only custom fields: `tbo_location_accuracy` (GPS accuracy in m, after Latitude), `tbo_nearest_location` (Shift Location) and `tbo_distance_m` (after Longitude), and `tbo_outside_radius` (after Place, a standard filter).
- HRMS fields (`latitude`, `longitude`, `geolocation`, `shift`, `offshift`) are untouched.

## 2. Radius enforcement

### Which locations apply

1. The **Shift Location** of the employee's submitted, active **Shift Assignment(s)** covering the day. HRMS 15 keeps the location on Shift Assignment only (Shift Type has none). The date, not the punch's shift window, decides, so an early or late punch is checked too.
2. If there is none: **TBO Smart Settings → Allowed check-in locations** (a list of Shift Locations), so check-in is limited before shifts are assigned.

Inside **any** applicable location is allowed. A Shift Location without coordinates or with a radius of 0 is ignored (HRMS also treats 0 as "no limit"); the settings refuse to list one.

### Distance

Haversine (HRMS's `get_distance_between_coordinates`) from the reported point to the centre.

- **Allowed:** distance ≤ `checkin_radius` + min(reported accuracy, **50 m**).
- **Too vague:** reported accuracy worse than **Worst GPS accuracy accepted** (default 100 m) is refused first: "Location not precise enough (±150 m; ±100 m or better needed). Move outdoors or turn on precise location, then try again." (`CheckinAccuracyError`, HTTP 417).
- **Outside:** "You're 1.2 km from Kottayam Office (allowed 100 m). Check-in not allowed." naming the nearest location (`CheckinOutsideRadiusError`, a subclass of HRMS's `CheckinRadiusExceededError`, HTTP 417).
- **No coordinates:** refused with "Turn on location to check in. Check-in is allowed only at …".

The accuracy comes from the app (`accuracy`, stored in `tbo_location_accuracy`). A punch without one gets no tolerance. A client could claim a better accuracy than it had; that gains at most 50 m and is no worse than a faked GPS position, which no server can detect.

### Where it is enforced

`doc_events` on Employee Checkin (`tbo_smart/checkin/employee_checkin.py`): `validate` applies the rule and `after_insert` queues the place name. They run after HRMS's own `validate`.

- It applies to **self check-ins**: the session user is the employee's own user. That covers the app, the HRMS PWA, desk and `/api/resource`, so none of them bypasses the rule.
- Logs entered for someone else (HR correcting a punch, a biometric device's integration user) are left to HRMS and DocType permissions.
- The check-in API needs no separate check: `check_in` inserts through the same document events.
- **Why document events, not a controller override:** `override_doctype_class` would let only one app own Employee Checkin and would break silently if HRMS renamed its method; Frappe's semgrep rules block it. The cost is that HRMS's own radius check still runs first (next point).
- **HR Settings → Allow Geolocation Tracking.** With it **off** (recommended with this rule), HRMS checks nothing and TBO Smart's rule is the only one. With it **on**, HRMS also refuses any punch outside the employee's *Shift Assignment* location, strictly: no accuracy tolerance and no exemptions (an exempt role or remote employee with a Shift Assignment location is then refused by HRMS). The fallback list is unaffected. Turn it on only if you need HRMS's GeoJSON map field, or if staff still punch from the HRMS PWA (which sends coordinates only when it is on).

### Exceptions

- **Roles that may check in anywhere** (settings, one per line, default `HR Manager`; add e.g. `Field Staff`).
- **Employee → Allow remote check-in** (`tbo_allow_remote_checkin`) for people working from home.

Their punches are accepted wherever they are. When outside, `tbo_outside_radius` is ticked, with the nearest location and distance, so HR can filter them.

### Switch and safe rollout

**Enforce check-in radius** is on by default, but it only acts when at least one usable location applies to the employee. With no Shift Location on any assignment and an empty fallback list, nothing is blocked, so deploying this cannot lock anyone out. Turning the switch off restores HRMS's behaviour exactly.

New Single fields get their defaults written once by `setup_checkin_location` (after install and every migrate), only where no value was ever saved.

### In the app

Before the punch the app calls `ess.get_checkin_locations` and measures the same way on the phone:

- inside: "Inside Kottayam Office · 35 m from the centre";
- outside: "You're 350 m outside Kottayam Office" with the direction ("to the north-east") and the check-in button disabled, plus **Refresh location**;
- too vague: the precision message, button disabled, **Refresh location**;
- exempt: "You can check in from anywhere", button enabled.

The server stays authoritative; its refusal message is shown if the phone and server disagree.

## 3. Owner setup on tboindia

1. **HR → Shift Location**: one per office or client site, with **Latitude**, **Longitude** (or the map) and **Checkin Radius** in metres (100 m is a good start; GPS indoors is often ±20–50 m).
2. Either put that Shift Location on each **Shift Assignment**, or, before shifts are assigned, list them in **TBO Smart Settings → Check-in location → Allowed check-in locations**.
3. **Roles that may check in anywhere**: keep `HR Manager`; create a role such as `Field Staff`, add it here and give it to field staff.
4. Tick **Employee → Allow remote check-in** for anyone working from home.
5. Optional: **Contact email for reverse geocoding** (an IT mailbox), and **Reverse geocoding URL** if you host Nominatim.
6. HR Settings → **Allow Geolocation Tracking**: leave it **off** unless you need it (see §2, "Where it is enforced"): with it on, HRMS adds its own strict check for Shift Assignment locations that ignores the exemptions and the accuracy tolerance.

## 4. Sites without HRMS or CRM

Nothing here uses the CRM. The custom fields and settings defaults are created only when `hrms` is installed.

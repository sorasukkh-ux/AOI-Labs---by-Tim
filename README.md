# AOI Labs - by Tim

Rice mill system: milling, buying, selling, inventory and finance.
This is the **base project**: Python (Flask) + MongoDB + HTML5/JavaScript, organised as **MVC**.

It follows the design prototype (`AOI Labs - by Tim.html`) and **AOI DB Structure 1.26**.

## What is built

| Area | Screens | Status |
|---|---|---|
| Sign-in | User ID, password, operating branch; account lock after 5 failed attempts | Built |
| Home | Menu groups and sub-groups, filtered by the role's View permission | Built |
| Administration | Milling Production, Milling Maintenance, User Maintenance, Role Authorization, Customer Registration | Built |
| Service | Customer Service (milling order registration with label preview; Buy and Sell tables), Milling Order List (editable details, delivery time confirmation, status with hold reason and inventory inbound at Finished, priority and pickup method on each card, details pop-up, cancellation requests, delivery details), Buying/Selling Order List (take-home bran/broken rice orders) | Built |
| Service | Current Services (prices and the queue status in one screen, refreshes every 30 s) | Built |
| Inventory | Inventory List (add, edit, transfer), Historical, Dashboard | Built |
| Financial | Pricing (market prices from the Thai Rice Millers Association, profit check), Market prices (source and mapping), Margin, Account, Marketing, Investment & Break-even (draft) | Built |
| Automation | AI (forecast, price advice, alerts, ask the system), Hardware Control (machine status, start/stop), Electricity (meter readings, rate, cost, post to Account), Machine Usage Statistics | Built, ready to connect devices |

Every menu opens its screen. A menu added later without a screen appears with a dashed border and opens a "not built yet" page.
Their collections and indexes already exist, and prices, the milling promotion and the account ledger
are already used by the milling orders (prices and promotion come from the starting data).

## Start it in GitHub Codespaces

1. Open the repository's codespace.
2. Put the project files in the codespace (first time only): drag `aoi-labs-base.zip` into the file list
   on the left, then in the terminal run:
   ```bash
   unzip -o aoi-labs-base.zip && rm aoi-labs-base.zip
   ```
3. Start everything:
   ```bash
   bash start.sh
   ```
   The script installs the Python packages, creates `.env` with a random secret key, starts MongoDB,
   adds the starting data and starts the server.
4. When the terminal shows `Running on http://127.0.0.1:5000`, open that address (a pop-up offers
   "Open in Browser"; the PORTS tab lists it too). The address is private to your GitHub account.
5. Sign in. Sample users (password `aoi12345` for all, change them in User Maintenance):

   | User ID | Role | Authorized branch |
   |---|---|---|
   | `admin` | Administrator | All factories |
   | `prasert.m` | Manager | All factories |
   | `somchai.k` | Operator | Factory 1 |
   | `wanida.s` | Accountant | Factory 2 |

6. Save the files to GitHub:
   ```bash
   git add -A && git commit -m "Base project" && git push
   ```

Stop the server with Ctrl+C. Start it again any time with `bash start.sh`.

### If MongoDB does not start

`start.sh` uses a `mongod` program if the codespace has one, otherwise Docker (`mongo:7`).
If neither is available it stops and lists the options:

- **Rebuild the codespace**: Command Palette, "Codespaces: Rebuild Container". The file
  `.devcontainer/devcontainer.json` uses GitHub's standard codespace image, which already has Python, pip
  and Docker, so `bash start.sh` works afterwards. (Do not add the "docker-in-docker" feature to it: on this
  image it fails to install and the codespace opens in recovery mode, without pip or Docker.)
- **Codespace in recovery mode** (the terminal shows Alpine Linux, "No module named pip"): make a new codespace
  instead. Push your work first (`git push`), or on github.com/codespaces use "Export changes to a branch".
- **Use MongoDB Atlas**: put the connection string in `.env` as `AOI_MONGO_URI`.
- **Look at the screens without a database**: `bash start.sh demo` (sample data, nothing is saved).

## Check that everything works

```bash
bash start.sh test
```

This runs the automated tests twice: on the in-memory stand-in, then on your MongoDB (each test in its
own throw-away database `aoi_test_...`, removed afterwards). **Run this once in the codespace**: the
project was developed where no MongoDB was available, so the tests have only been run on the stand-in.

## MVC structure

```
app/
  models/            MODEL        one module per collection + the business rules
    rules/                        plain functions: yield, charges, status flow, receive time
    schema.py                     every collection of DB Structure 1.26 and its indexes
  controllers/       CONTROLLER   URL routes: check permission, call models, choose a view
  views/             VIEW
    templates/                    HTML5 pages (Jinja), with the prototype's markup
    static/                       the prototype's stylesheet, JavaScript, logo
  translations/                   th.json, en.json (interface text)
  security.py                     session, role permissions, CSRF protection
  db.py                           MongoDB connection
  memory_db.py                    in-memory stand-in used by the tests and demo mode
scripts/seed.py                   starting data
tests/                            automated tests
run.py, start.sh                  start the server
```

How one request travels, for example saving the output ratios:

1. **View**: `views/templates/admin/milling_production.html` posts the form.
2. **Controller**: `controllers/admin_milling.py` checks the role has Edit on `AD_MILLPROD` and that
   the user may maintain that branch, then calls the model.
3. **Model**: `models/mill_yield.py` applies the rule in `models/rules/yield_rule.py`
   (the four ratios must not total more than 100%) and writes `AOIMS_MILLYIELD`.
4. **Controller** redirects back; the **View** shows the saved values or the error message.

### Adding a screen

1. Model: add `app/models/<name>.py` (reads and writes of its collection, validation).
2. Controller: add `app/controllers/<name>.py` with a blueprint, protect each route with
   `@require("MENU_CODE", "view" | "create" | "edit" | "delete" | "approve")`, register it in
   `controllers/__init__.py` and map the menu code in `controllers/common.py` (`MENU_ENDPOINTS`).
3. View: add templates under `app/views/templates/`, text keys in both translation files.
4. Add tests in `tests/`.

## Rules carried over from the design

- **Branch**: the operating branch is the one chosen at sign-in. A user fixed to one branch always
  works there. Only maintenance screens have a branch selector, limited to the user's branches.
- **Permissions**: checked on the server for every request. `ADMIN` always has full access. Cancelling
  an order needs the Delete right; answering a cancellation request needs the Approve right.
- **Output ratios**: each 0 to 1, total not more than 1.
- **Payment**: milling/packaging fee (rice output rounded up to full units) + bran and broken rice
  taken home (selling price, milling promotion discount up to the customer's own output) + delivery
  fee (distance x rate, delivery orders only). Posted to the account ledger when the order is closed.
- **Receive time**: orders In-process first, then the Queue by priority and registration time; milling
  only inside operating hours; rounded up to the next receiving period.
- **Status flow**: Queue -> In-process/Hold; In-process -> Hold/Finished; Hold -> Queue/Cancelled;
  Finished -> Ready for Delivery (delivery) / Closed (take-away) / Cancelled; Ready for Delivery -> Closed.
- **Linked orders (update 28)**: a milling order with take-home bran/broken rice cannot be Closed or Cancelled
  until its buying/selling orders (SL-...) are Closed or Cancelled; the order card shows which ones are still open.
- **One take-home order (update 39)**: the bran and broken rice a milling customer takes home are ONE selling order
  with a line per SKU (`ORD_ITEMS`). **Edit items** on the Buying/Selling Order List changes the kg per line while
  the order is Open or Prepared (charged as at registration: milling discount up to the customer's own output); at
  Prepared the stock kept for the customer is corrected; the milling order's take-home kg and charges follow.
  Prepared asks the kg received per line, one location. **Update 42:** a buying/selling order is prepared and closed on
  its own, without waiting for the milling order's status. Earlier orders (one per SKU, still Open) are merged once by
  `start.sh`.
- **Two views (update 46)**: the Milling Order List has *Active* (Queue, In process, Hold, Finished, Ready for
  delivery - in the list sequence below) and *Closed / cancelled* (newest first, the latest 200; search for older),
  each with its status chips and counts. A scanned label of an order in the other view opens that view.
- **List sequence (update 44)**: Milling Maintenance > Milling order list sequence, per factory: *material type base*
  (variety Jasmine, White, Glutinous > status > priority), *order status base* (status > variety > priority, the
  default) or *order priority base* (priority > variety > status). Orders waiting for an answer stay on top; the
  registration time decides last. It orders the Milling Order List, Current Services and the TV; receive times are
  calculated as before.
- **Numbers**: order ID `ML-YYYYMMDD-NNNN` (queue number `NNNN` restarts each day),
  customer code `C` + `YYMMDD` + 3 digits, QR text `AOI-ML-YYYYMMDD-NNNN`.

## Screens and the design prototype

The built screens use the prototype's own stylesheet (`app/views/static/css/app.css` starts with it,
unchanged) and its markup: the same buttons, cards, chips, coloured status drop-down, pop-up and
printed QR label. Differences that remain, and why:

- **Customer on a milling order**: must be chosen from the customer master (Scan QR, or search by
  name), because `AOITS_ORDMILLING.CUST_CODE` is mandatory. A walk-in name cannot be typed in.
- **Print QR** saves the order, prints its label (with the real queue number; the preview at Review
  shows the next free number) and then asks whether to go to the order list or register the next order.
  For a delivery order the button reads Confirm Order and nothing is printed.
- **Scan QR** chooses the customer when the full customer ID is entered and lists the matching
  customers for a partial ID.
- **New customer from Customer Service**: after saving, a pop-up asks whether to go back to Customer
  Service with that customer chosen.
- **Order details** open as a pop-up on the list, as in the prototype; each order also has its own
  page (used for reprinting the label).
- **Ready for Delivery card**: the address and distance can be corrected ("Update delivery details").
  The prototype's arrival-time confirmation is not built (the DB structure has no field for it).
- **Sign-in**: the branch list does not change with the typed user ID (that would reveal which user
  IDs exist), and there is no demo-account list. A user fixed to one branch is signed in there.
- **Receive time** fixes two prototype defects: In-process orders come before the Queue, and the real
  date is shown instead of "(+1d)". An order longer than one working day continues the next day.
- **Cancellation requests** normally come from the LINE app. Until that exists, the order details
  have a "Record cancellation request" button.

## Differences from the DB structure

- **One extra collection**, `AOISYS_COUNTER`, keeps the running numbers. It is not in the workbook.
- **Names** (customer, user, role) are stored once, as typed; they are not translated.
- **Not built yet**: LINE notifications, adding milled output to inventory, and writing
  `AOITS_QR_PRINT` records (the label is drawn from the order). `REMARK` is not on the form.
- **Manager role**: the sample Manager also has the Delete right on Milling Order List, so a
  manager can set Cancelled after approving a request.

## Rice varieties, milling tests and market prices

- **Varieties**: jasmine (HM), white (WH) and glutinous (GL). Paddy, rice and broken rice are separate SKUs per
  variety (`PADDY_HM`, `RICE_HM`, `BROKEN_HM` ...; the white-rice SKUs keep the old codes `PADDY`, `RICE_W5`,
  `BROKEN`); bran and husk are shared. A milling order has a variety: it decides the yields, the rice SKU received
  at Finished and the broken rice SKU the customer takes home. Old orders and data are white rice.
- **Milling tests** (Administration > Milling Production): paddy in, minutes, outputs out. The yield ratios of each
  variety and the minutes per kg (Milling Maintenance) become the weighted average of the tests. Outputs not
  weighed keep the typed ratio. Starting data: the mill's three tests (33 kg/9 min/17 kg rice/2 kg broken,
  36 kg/10 min/20.5 kg rice/2 kg broken, 39 kg/12.75 min/22.5 kg rice/3 kg broken): rice 55.56%, broken 6.48%,
  0.294 min/kg.
- **Market prices** (Financial > Pricing > Market source and mapping): the server downloads the newest
  `Pricerice<date>.pdf` from the Thai Rice Millers Association price page once a day (from `AOI_MARKET_HOUR`,
  `AOI_MARKET_AUTO=1`) and on **Fetch now**; the PDF can also be uploaded. The rows are read with `pypdf`;
  the mapping says which row and number is each SKU's market price (THB/100 kg x 0.01, THB/ton x 0.001). The
  suggestions are recalculated (market + margin) and changed prices **wait for approval**, as before. A price
  that moves more than 30% is not applied (shown for a check).
- **NABC daily farm prices** (source "NABC daily price" on Market prices): per SKU an exact NABC product name
  (from agriapi.nabc.go.th/home/price), optional provinces and median/average/low/high of the newest day's markets.
  `AOI_NABC_PRICE_URL` (default `https://agriapi.nabc.go.th/api/daily-prices/product`) is called with
  `?product_name=...&page=n`; prices in บาท/ตัน become THB/kg (x 0.001). The daily check reads page 1 only and
  downloads further pages only when page 1 has a newer day than the one stored. Jasmine paddy uses
  `ข้าวเปลือกเจ้าหอมมะลิ 105` by default; the other SKUs stay on the association report until their NABC name is set.
- **Profit check** (Pricing): per variety, the value of 1 kg paddy once milled (yields x selling prices), the
  processing cost (electricity from the meters or rated kW x minutes per kg, plus the other cost per kg on
  Milling Maintenance), the break-even and the recommended buying price (the lower of the market suggestion and
  break-even minus the paddy's buying margin target), profit per kg/ton and the rice break-even selling price.
  A paddy price waiting for approval starts from the recommended price. With an AI key, **Ask AI to review**
  sends these figures to Ask the system.

- **Labour cost** (Milling Maintenance): labour per worker per day (600 THB), workers per day (2) and planned paddy
  per day (2,000 kg). The profit check counts labour per kg = labour per day / planned paddy per day and shows the
  real average of the days with orders in the last 30 days beside it. Planned 0 = use the real average.
- **Electricity in the profit check**: from the meter readings when there are any; otherwise the monthly bill on
  Milling Maintenance (9,000 THB) / (planned paddy per day x working days per month, 26) = 0.17 THB/kg.
- **Supply indicator** (Automation > AI): the monthly agricultural production index of the National Agricultural
  Big Data Center (`AOI_NABC_INDEX_URL`, default `agriapi.nabc.go.th/api/production-index-month/sector`), fetched
  once a day with the market prices and on Fetch now. It is an index for the whole sector, not a price: compared
  with the same month last year it shows whether supply is up (paddy prices may soften) or down; more than 10%
  gives an alert, and Ask the system receives it.

## TV display per factory

Each factory has its own full-screen TV page for the waiting area: `/tv/factory1?key=...` and `/tv/factory2?key=...`.
Administration > Milling Maintenance > TV display shows the link of the chosen factory and issues a new key (the
old link stops at once). The TV does not sign in; open the link in the TV's browser and press **Full screen**
(top right, shown while the mouse / remote pointer moves; or double-click, or the F key) - like the SCADA.

**Testing in GitHub Codespaces:** the link shown is the codespace's forwarded address (it is built from the address
in your browser). A TV is not signed in to GitHub, so make the port public first: Ports tab > right-click port
5000 > Port Visibility > Public. The codespace still sleeps when it is idle and its address can change, so this is
for testing; for TVs that run all day the system needs a permanent server.

- Drawn for 1920 x 1080 and scaled to the screen (720p, full HD or 4K); dark, large text for a 50" TV at 3-5 m.
- Three columns: finished, ready to collect (newest first, marked for 5 minutes) - milling now (progress from the
  milling time per kg) - next in queue (pickup time); long columns turn pages every 10 seconds.
- Prices of the day at the bottom; the data refreshes every 15 seconds without reloading; the clock follows the
  server's time zone. Customer names show the first name and the surname's initial only (public screen).

## QR label, scan to start, and the status timeline

Every milling order (take-away and delivery) prints a QR label at registration; it goes on the customer's paddy
and is the key between the order and the material. To set an order to **In process**, the operator scans that
label (a handheld scanner types it; the code can also be typed: `AOI-ML-YYYYMMDD-NNNN`) and weighs the paddy.
A label of another order is refused. The rice received at Finished is kept as the actual rice.

Every status change is written to `AOILOG_MILLSTATUS` (from -> to, time, user, reason, QR scan, weights, minutes
in the previous status) and shown as a timeline in the order details. **Administration > Milling > Milling Analysis** (tabs: analysis by period, milling today, buy/sell today; it replaces the Service Dashboard) shows, for a
period, waiting and milling times, holds, cancellations and the real yield against the expected one, per rice
variety and per customer, with CSV downloads (orders, and every status change). Orders made before this update
get their timeline once from `AOILOG_ORDMILLING` when `start.sh` runs.

## Scan QR with the iPad / phone camera (update 37)

Every scan field has a camera button next to it: New Order "Scan QR" (customer card), Customer Maintenance,
the Milling Order List search, the Buying/Selling Order List search, "Change customer" in the order details and
the paddy label at **In process**. Tap it, hold the QR inside the frame: the code is read in the page
(`static/js/qrdecode.js`, written for this project - no library or outside service, the picture never leaves the
iPad) and fills the field (read twice the same, so a half-seen code is not used). Then the field does what Enter
does: New Order picks the customer, the list searches run. A Bluetooth / USB scanner (keyboard mode) still works
in the same fields without the button.

The **Milling Order List** has a search (order ID, queue no., customer code or name). Scanning the QR label on the
paddy bag (`AOI-ML-...`) shows that order and opens its details at once; status changes keep the search.

The browser gives the camera only on a secure link: `https://...` (GitHub Codespaces is https) or `localhost`. On a
plain `http://192.168.x.x` address the button explains this; the server for the mill needs https (a certificate,
e.g. Caddy or a Cloudflare tunnel) for the camera. First use: Safari asks to use the camera - **Allow** (iPad
Settings > Safari > Camera).

## LINE messages to customers

When a take-away order is set to **Finished** the customer gets "your rice is ready"; for a delivery order the
message goes when the operator confirms the delivery time on Ready for Delivery. Each message is kept in
`AOITS_NOTIFY` and shown on the Milling Order List card (LINE Sent / LINE not sent / LINE Failed, with
"Send LINE again"). A LINE problem never stops the status change.

LINE Notify closed in March 2025, so the messages go through the **LINE Messaging API** of the mill's
LINE Official Account (push message):

1. Create a LINE Official Account for the mill and turn on its Messaging API (LINE Developers console).
2. Copy the channel access token into `.env` as `AOI_LINE_CHANNEL_TOKEN`, then restart (`bash start.sh`).
3. Put the channel secret in `.env` as `AOI_LINE_CHANNEL_SECRET` and the account's basic ID (for example
   `@783ycdvd`) as `AOI_LINE_OA_ID`, restart, and make port 5000 Public in Codespaces.
4. Administration > Milling Maintenance > **LINE messages to customers** shows the checklist and the
   **Webhook URL** (`https://<server>/line/webhook`). Paste it in LINE Developers > Messaging API > Webhook URL,
   tap **Verify**, turn on **Use webhook**, and in LINE Official Account Manager > Response settings turn the
   default auto-response off (otherwise both answer).

**How customers link their LINE** (LINE never shows a user ID, so the customer links themself):
- the queue label has a second QR "LINE": the customer scans it, adds the account and taps Send; the ready-made
  message `LINK <order>-<check>` links that order's customer (the 6-character check stops guessed order numbers);
- or, from the counter poster (Milling Maintenance > Print poster), the customer adds the account and sends the
  mobile number registered at the mill (only when exactly one customer has it and it is not linked to another LINE).
The system answers in the chat (reply messages are free). A "rice ready" message that could not be sent because
the customer was not linked is sent again right after linking. Every event is listed on the setup card
(`AOILOG_LINE`). The order card shows why a message was not sent.

Push messages count toward the Official Account's monthly quota (replies do not). For daily use the system needs
a permanent server: a sleeping codespace cannot receive LINE's calls.

**LINE Message Maintenance** (Administration > Customers): every text the customers receive (order messages and chat
replies) with its message ID, name and {parameters} (queue no, customer, time, date, rice kg, amount, factory,
address). A preview shows sample values; the order messages can be switched off; "Back to default" restores the text.
Once a customer is linked, their new queue tickets no longer show the LINE QR, and the customer record keeps the
LINE display name and how it was linked.

**Link another LINE account** (Administration > Customer Registration > the customer > LINE account): shows a QR
with `LINK <customer code>-<check>`. The customer scans it with the new LINE and taps Send; the new account replaces
the old one (`STAFF_QR`). "Remove LINE link" clears it, so the next queue ticket shows the LINE QR again.

## Queue QR to the customer's LINE (update 47)

When a milling order is registered (Print QR) and the customer is linked to the mill's LINE OA, the customer also
gets the queue number, payment and receive time (message **ML_LABEL**, editable on LINE Message Maintenance) **and the
QR picture** of the label, to show when collecting. The order details have **Send QR to LINE** to send it again.
LINE downloads the picture from the app (`/line/qr/<order>.png?t=<secret token>`), so the app needs a public https
address: `AOI_PUBLIC_URL` in `.env`, or else the address in the staff's browser (in Codespaces: port 5000 **Public**,
as for the TV). Without one, the text is sent alone. The picture is made by the server (`app/models/qr_png.py`, the
same QR as the printed label; no extra package).

## Server health on AI (update 48)

Automation > AI starts with **Server health**: disk, memory (RAM), CPU, the database answer time, the app's errors and
warnings of the last hour (the log list below it), and machine devices that stopped reporting. The server checks itself
every 5 minutes (and **Check now**); limits: disk / memory 85 % warning, 95 % critical; CPU 85 / 97 %; database
500 / 3000 ms; errors 1 / 20 per hour; warnings 20 / 200. When something is not ok, the **home menu shows a red "!"**
on AI (and its group) and a banner with the problem - for everyone who can open AI. With an AI key, **Ask AI what to do**
explains the risk and the next step (the health figures are part of what the AI sees). The app's own warnings and errors
are kept in `AOITS_SYSLOG` (14 days), the checks in `AOITS_HEALTH` (7 days).

**Customer card to LINE:** Print QR card also sends the card (text LN_CARD + the QR picture of the customer code) to the
customer's LINE when "Also send to the customer's LINE" is ticked (shown for customers linked to LINE); "Send to LINE
only" sends without printing. The same public https address is needed as for the queue QR (update 47).

## Campaign levels are ranks (update 95, DB 1.62)

- Level 1 / 2 / 3 = rank 1 / 2 / 3, ONE customer each: the customer with the most points per order type gets the level 1 gift, the next one level 2, then level 3 (ties: more kg, then who got there first).
- Per type: "minimum points" to be ranked or enter the draw (instead of points per level), and the 3 rank gifts.
- During the campaign the table shows the rank so far; after the last day the ranking is final, gifts can be marked as given (one per rank) and the special gift drawn. The rank 1-3 winners are not in the special draw.

## Campaign points table before the first save (update 94)

- The customers' points card now shows before the campaign is saved, with what to do (fill the gifts, switch the campaign on, Save). An empty table also says why: campaign off, not started yet (points count from the start date) or ended.

## Christmas / New Year campaign (update 93, DB 1.61)

- Financial > Marketing > "Christmas / New Year campaign" (right: Marketing; Edit to change, hand gifts over and draw).
- Set-up: name, dates, kg per point (default 10 kg = 1 point), on / off; for each order type - Milling, Buying (from customers), Selling (to customers) - 3 gift levels (points + gift you type) and one special gift.
- Points add up per customer and type during the campaign: milling = finished orders (weighed paddy, else registered), buying / selling = closed orders. The customers' table shows kg, points, level medals, points to the next level and the gifts earned; "mark as given" records the hand-over (once per level).
- Special gift: Open the lucky wheel. Customers who reached level 1 are on the wheel, slice size = points. SPIN asks the server, which draws the winner once (secure random, weighted by points) and keeps it; the wheel spins with ticks and stops on the winner, then confetti, a fanfare and the winner card. Afterwards the wheel only replays the same result.
- New collections AOIMS_CAMPAIGN, AOITS_CAMPGIFT, AOITS_CAMPDRAW.

## Ranking: buying and selling take turns (update 92)

- The ranking theme now shows both lists: the selling-price ranking (blue), then the buying-price ranking (green), switching after the highlight has walked through the list. Only one list when only one side has prices. Five rows or fewer are drawn bigger.

## Billboard settings layout (update 91)

- Milling Maintenance > Price billboard settings is tidied into 4 numbered sections: 1 products and prices (compact table; the buying / selling price is a green pill when shown, struck through when hidden), 2 display (theme, seconds, promotion switch), 3 Christmas music (switch, volume with live %), 4 running messages; Open billboard and Save sit together at the bottom. No change to what is saved.

## Billboard themes: price cards and ranking (updates 89-90, DB 1.60)

- The Poster wall theme is removed (a saved "poster" shows as Price cards).
- Price cards (theme cards): light background with colourful wavy corner shapes and white dots / + / x / zigzag, three white cards at a time, each with a spinning starburst badge with the price, the product name, buying / selling rows with tick icons, the change and the note, a 30-day line and a round product button. Cards bounce in and the page turns with the spotlight time; dots show the page.
- Price ranking (theme ranking): "Today's price ranking - Top N" list sorted by the selling price (or buying when only buying is shown), bars growing to the price, the highlight walking down the list, tilted signs with the product names on the left, a bright hall with a moving floor grid and light beams. Original designs, no brand logos.
- Choose in Milling Maintenance > Price billboard settings > Billboard theme, or try &theme=cards / &theme=ranking.

## Christmas music (update 87, DB 1.59)

- Price billboard settings: "Christmas music" on / off and volume (0-100). With the Christmas theme the billboard plays soft music-box carols made in the browser (no audio files): Jingle Bells, Silent Night, We Wish You a Merry Christmas, Deck the Halls (public domain).
- Browsers allow sound only after one tap / click / remote key on the TV: the "Tap for music" button blinks until then. The speaker button mutes / unmutes that TV (remembered on it). For a kiosk without anyone to tap, start Chrome with --autoplay-policy=no-user-gesture-required.

## Brighter Christmas theme with 3D gifts and trees (update 86)

- Brighter, more colourful: red / pink / green / teal background with a warm glow, coloured bokeh lights twinkling, white and gold scribble frames, glowing mint and gold prices, a rainbow ticker, an orange-red spotlight row.
- 3D in the background (CSS 3D, no library): 12 gift boxes (6 faces with ribbons and a bow), Christmas trees (two crossed planes) and shiny baubles drift through the room at different depths while they spin; they show through the panels.
- Same switch as before (Billboard theme = Christmas, or &theme=christmas). Turned off on screens set to reduce motion.

## Christmas theme for the price billboard (update 85, DB 1.58)

- Milling Maintenance > Price billboard settings > "Billboard theme": Standard (like the TV) or Christmas. The billboard changes within 15 seconds.
- Christmas: dark green / red night, hand-drawn scribble frames around the panels (drawn in when the page opens), falling snow, blinking string lights, holly, a little tree, a "Merry Christmas" greeting, candy-stripe timer and Mali handwriting font. Original drawings (not the Adobe Stock template, which is licensed artwork).
- Try it without saving: add &theme=christmas to the link. Saved in SYSP_BB.THEME.

## Price billboard settings (update 84, DB 1.57)

- Administration > Milling Maintenance > "Price billboard settings" (needs Edit on Milling Maintenance), per factory:
  - per SKU: Show, Order (1 = first), buying price and / or selling price, and a promotion note (shown as a yellow ribbon on the spotlight and in the ticker);
  - seconds per SKU in the spotlight (4-30);
  - the milling discount promotion on / off;
  - up to 3 running messages (they take turns at the top and run in the ticker).
- Saved in AOIMS_SYSPARAM.SYSP_BB; the billboard follows within 15 seconds. &show=buy / &show=sell on the link still narrows it further.

## Price billboard - moving prices in the TV theme (update 83)

- New page /tv/<branch>/prices?key=<TV key> (no sign-in, the same display key as the TV). Open it from Administration > Milling Maintenance > TV display > "Open the price billboard".
- Spotlight: one SKU every 7 seconds slides in with its picture, buying and selling price in rolling digits, the change from the previous price (up / down, %), and the 30-day trend drawn in. A timer bar shows when the next SKU comes.
- Right: all prices, the spotlight row highlighted. Bottom: a ticker that scrolls all prices. The milling promotion (take-home discount) shows at the top when one is running.
- Refreshes every 15 seconds; a price changed on Pricing Maintenance flashes on the board and takes the spotlight with a "New price" badge.
- &show=buy or &show=sell shows only the buying (ราคารับซื้อ) or selling price; default both. Full screen: button, double-click or F; arrow keys move the spotlight. History from AOILOG_PRICE (no DB change).

## Buy / Sell table column order (update 82)

- Customer Service > Buy / Sell: columns are now Product, Price, Quantity (kg), Sub-total. Tapping a quantity of 0 selects it, so typing 30 gives 30 (not 030).

## Export (FOB) price check (update 81)

- AI > Price analysis: when the latest export price of a SKU is below 65% or above 180% of its home price (millers' report, applied, NABC or AI web), a warning shows the export row, US$/ton and the exchange rate used, and the export price is NOT used as the base of the signals.
- Base source order is now home first: NABC, millers' report, applied, AI web, then export. With fewer than 20 prices, a home source with 2+ prices is used before the export price.
- First guess of the export row: a whole-grain SKU never takes a broken rice row (ปลาย / A1 Super), and the plainest matching row is chosen.
- To fix the jasmine row yourself: Price analysis > Sources > export rows - choose "Hom Mali 100%" for Jasmine rice, and check the USD/THB rate (about 32-33 in Oct 2026).

## Milling Analysis as a dashboard (update 80)

- AI > Milling Analysis > Analysis by period is now a wide dashboard: 8 KPI tiles (orders with a finished / in progress / cancelled bar, paddy -> rice, real yield coloured against expected, QR scan ring, waiting, milling, registration to ready, held).
- Charts: paddy per day (bars) with the real yield line and the expected yield (dashed) - per week for periods over 62 days; where the time goes (queue / milling / hold-finishing / ready-to-closed); rice varieties (paddy share donut, yield vs expected scale, waiting and milling bars, held / cancelled); registrations by hour (busiest hour); top 8 customers by paddy.
- Colours: green = yield within 0.5 pt of expected, amber = up to 2 pt below, red = more. Hover a bar for its numbers. The number tables are still there under "Show the number tables"; CSV export unchanged. Drawn on the server as SVG (no chart library).

## Bran / broke price typed by hand on the milling order - super user (update 79, DB 1.56)

- Same right as update 78: Administrator, or a role with **Approve** on Customer Service.
- Customer Service > Milling: new boxes **Bran price** and **Broke rice price** (THB/kg). Blank = branch price (shown in grey, follows the variety). A typed price needs a reason; Review shows the charge at that price.
- Update Milling Status > order details > Edit details: the same two boxes while the quantities can be changed. Clear them to go back to the branch price. Changing only the price keeps the queue and receive time.
- The take-home selling order line uses the typed price (UNIT_PRICE) and keeps the branch price (LIST_PRICE); the details show "Price set by hand ... by <user>: <reason>".

## Price typed by hand on a buy / sell order - super user (update 78, DB 1.55)

- Who: the Administrator, or a role given **Approve** on Customer Service (SV_SERVICE) in User Authorization. Everyone else sees the branch price only (the server refuses a changed price: 403 / "No right to change the price").
- Where: Customer Service > Buy / Sell - the price cell of each SKU is a box (branch price filled in). A changed price is highlighted and needs a **reason**.
- Later: Buy / Sell order list > **Change price** on Open / Prepared / Unpaid orders (not take-home orders of a milling order). Amounts are recalculated; the reason is required.
- Range 0.01 - 1,000 THB/kg. The order keeps the branch price (LIST_PRICE) per line and PRICE_OVR_FLG / USER / DATE / REASON on the header; the list shows "Price set by <user>: <reason>".

## Market prices found on the web by AI; white paddy row fix (update 77)

* **AI > Price analysis > Market prices found on the web by AI**: once a day (and with "Search prices now") an AI that
  searches the web finds today's market price of every traded SKU in the mill's area (AOI_AI_PRICE_REGION, default
  Khon Kaen / Isan): THB/kg low-high, the date and the source link. Gemini with Google Search when
  `AOI_GEMINI_API_KEY` is set (Google AI Studio; model AOI_GEMINI_MODEL, default gemini-2.5-flash), else Claude with web
  search through the Ask AI key `AOI_AI_API_KEY`. A value needs a source, a believable THB/kg (per ton / per 100 kg
  are converted) and a recent date. It is drawn as its own line (AIWEB, purple) and goes into Ask AI.
* **Monthly history, 3 years (estimate)**: one request for the monthly averages of every SKU (AIHIST, dashed) so
  every SKU has a trend line at once - an estimate, for the trend only.
* The signals now use the first source with at least 20 prices (unless one is chosen in the settings), so a SKU with
  one applied price and a daily AI price uses the AI price instead of showing nothing.
* Rice millers' report: white paddy (key "ข้าวเปลือกเจ้า") no longer takes the jasmine row "ข้าวเปลือกเจ้าหอมมะลิ..." -
  a row with a grade word the key does not have (หอมมะลิ, หอม, ปทุม, เหนียว) is skipped. Price analysis warns when a
  white / glutinous price is 90% or more of jasmine of the same stage, and when a base price is more than 25% from
  the AI web price. After installing, check Financial > Pricing Maintenance: the white paddy market price changes.
DB 1.54 (AOITS_AIPRICE).

## Logo opens the home page (update 76)

Tapping the AOI logo (or the system name) at the top left of any screen opens the home menu.

## The mill's own trading on Price analysis (update 75)

AI > Price analysis has a new card **The mill's trading in AOI** for the SKU and period chosen: what the mill bought
(customer selling orders) and sold (customer buying orders) - kg, average THB/kg, value, orders and customers, and
the gap to the main market price on the same days; the margin sell - buy per kg, the estimated gross profit, net kg
bought, kg sold per day (30 days) and how many days the stock lasts; a bar per week (month for 3 years) - bought up,
sold down; and plain findings (buying above the market, selling below it, no margin, slow stock). Cancelled orders
are left out. The same figures (6 months) go to Ask AI with the market signals.

## One order per registration, all customers listed, QR picture (update 74)

* Buy / Sell on Customer Service: everything registered together for one customer is **one order** with a line per
  SKU (before: one order per SKU). Prepared receives every line into stock (customer selling) or takes every line
  out of stock and keeps it (customer buying - a "take from" choice per SKU; refused when any SKU is short, nothing is
  kept then); Closed posts the total. Orders registered earlier stay as they are.
* Customer search on Customer Service: with no name typed, the list shows every customer (it scrolls); a typed name
  shows the first 50 matches (it was 8, so names further down the alphabet were missing).
* Milling / Buying-Selling lists with a search: after a status change the list follows the order to the tab it moved
  to; the "close when ..." note names the open buying/selling order and its status (e.g. Unpaid) and links to it.
* The QR picture sent to LINE is now an RGB PNG (it was grey-scale). If the queue card still shows no QR, port 5000 of
  the Codespace must be Public (see update 67).

## Tabs with a search (update 73)

On the Milling Order List and the Buying / Selling Order List, a search jumps to the tab where the order is only right
after the search (or from a return link); tapping another tab afterwards stays on that tab, even with the search
still in the box (it shows 0 there - tap Clear to see all orders of the tab).

## Cancelled milling order: stock Pending maintenance (update 72)

Cancelling a milling order no longer leaves goods kept for the customer: the milled rice and the take-home bran /
broken rice (received by its buying order) become the mill's stock where they are, with the new status **Pending
maintenance** (P) - shown on the Inventory list, not sold until someone checks them and changes the status to
Available (or moves / adjusts them). Cancelling a take-home order after its goods were received does the same for
those goods. start.sh moves stock still kept for orders cancelled before this update. DB 1.52.

## SCADA / AOI 3D switch (update 69)

Both screens have a **SCADA | AOI 3D** switch at the top. Outside full screen it opens the other screen. In full
screen the other view opens inside the current one (the same screen with ?embed=1, without the page header), so the
full screen stays; tap the first tab to come back. Leaving full screen returns to the screen's own view.
Update 70: the 3D view inside SCADA keeps its Reset view / Top view / Auto-rotate buttons; Auto-rotate is
remembered in the browser. Update 71: the 3D full screen fits the screen (no scrolling; the 3D area takes the
rest), also inside SCADA; while SCADA is shown inside the 3D screen, the 3D buttons hide and the title follows the view.

## Take-home goods leave with the rice (update 68)

A customer buying order linked to a milling order (its take-home bran / broken rice, or linked by hand) keeps its
goods in stock after it is Closed (paid) - the card says "still in stock, picked up with milling order #...". They
leave the stock when the milling order is Closed (picked up with the rice) or Cancelled. An order that is not linked
releases its stock at Closed, as before. The income is posted when the buying order is Closed.

## LINE pictures need a public address (update 67)

LINE fetches the QR picture of a queue card / customer card from the app's https address without signing in. In
GitHub Codespaces a port is Private by default (also after it was removed and added again), so LINE gets GitHub's
sign-in page and the card shows an empty space where the QR should be. Fix: Ports tab → port 5000 → Port Visibility →
Public (or set AOI_PUBLIC_URL to a public address). After sending, the server now checks the picture address the way
LINE does and tells the staff when it is blocked.

## 3D plant (update 66)

Automation > SCADA > **3D view** (/automation/scada/3d) draws the plant in 3D from the same live data as the SCADA
(every 2 seconds): silos, conveyors, intake bin, sorter, the mill selector, main motor 1 / 2 with the mill, packer and
AMR, placed by their SCADA column / row and joined by the FEEDS_TO pipes. Colours follow the status; a machine with
a device moves only on fresh telemetry (the motor coupling turns at its output Hz, belts and the AMR move); rice flows
in the pipes of running machines, from the selector only to the motor it points at, and the selector's chute turns to
that motor. Drag to turn, pinch / scroll to zoom, two fingers to move, tap a machine for its details. Commands stay
on the SCADA screen. Own small WebGL code (js/plant3d.js) - no library, works without internet.

## Cancel reason, list tabs, LINE link notice, clearing transactions (update 65)

* Milling Order List: cancelling an order asks for the reason in the same pop-up (required, kept in CANCEL_REASON and
  on the CC status event; shown on the card and in the order details). An approved cancellation request suggests
  its hold reason. "Update delivery details" only opens the fields again - no "order updated" pop-up.
* Buying / Selling Order List: two tabs - Open / Prepared / Unpaid, and Closed / Cancelled (newest first, the
  newest 200). A search finds an order in the other tab.
* When a customer links their LINE account, the staff (Customer Service, order lists, Customers rights) get a notice
  on whatever page they have open (asked every 20 s): name, code, LINE name, how they linked, and "Open customer".
* Start again with clean data, keeping customers and every master:
  ```
  python -m scripts.clear_transactions          # shows what would be deleted
  python -m scripts.clear_transactions --yes    # backs up to backups/ (not sent to GitHub), then deletes
  ```
  It deletes milling and buying/selling orders with their history, QR prints, LINE messages, stock and the ledger,
  and restarts their running numbers (add --machine-data for machine commands, telemetry, meter readings and
  health history). Stop the server first.

## Hardware devices card (update 64)

AI > Hardware devices shows only the devices of the branch you work in (each factory has its own "main motor 1 / 2",
which looked like duplicates) - a line under the cards says how many are in other branches. The mill selector
(ชุดสลับเครื่องสี, motor switching device) is listed too, with the motor it points at; a stopped selector device is a
health warning like the motors.

## Live AI screens (update 63)

Everything on Automation > AI is read from the database and the server itself (orders, prices, the device reports, the
server's disk / memory / CPU) - nothing is demo data unless the server runs with AOI_DB=memory (the yellow banner).
Before, the server health shown was the check saved every 5 minutes (up to 10 minutes old) and a device counted as
connected until 5 minutes after its last report, so a stopped simulator still showed "normal" for a while.
Now:
* the health is checked when the screen asks (kept 10 seconds); the 5-minute check is still saved for the 24-hour chart;
* a device is offline 1 minute after its last report (the gateway reports every second) - the health turns to
  warning and the device card shows offline;
* the AI screen updates itself every 15 seconds (Price analysis 60 s, Milling Analysis 30 s) without reloading: only
  the parts that changed are swapped, the scroll position stays. It pauses while you type, a pop-up is open or a filter
  was changed and not sent yet, and while the tab is hidden;
* the green bar shows the time of the last update; when the server cannot be reached it turns red ("cannot reach the
  server since ...") and the banner shows "No connection".

## Receipt to LINE (update 62)

The receipt pop-up (Print Billing) of customer buying/selling orders has a **Send LINE** button next to Print. It sends the
receipt as a LINE card (Flex Message): logo, AOI Labs, address, receipt title and order type, order numbers, customer, date,
each line (kg x THB/kg = THB) and the total. The text LN_RECEIPT (LINE Message Maintenance) is the card's notification
preview. The logo needs a public https address (AOI_PUBLIC_URL or the Codespaces address); without one the card is sent
without the logo. The button is grey when the customer is not linked to LINE. Each send is saved in AOITS_NOTIFY
(NOTIFY_KIND receipt, ORD_TYPE BY / SL). DB 1.50.

## One broken rice SKU (update 61)

Broken rice is one SKU, **BROKEN (ปลายข้าว)**, for every variety - like bran and husk: one price, one stock, one
row in the Buy / Sell tables, and the take-home broken rice of every milling order. start.sh merges the old
BROKEN_HM / BROKEN_GL: their stock and open orders move to BROKEN, their prices and market mappings are removed
(check the BROKEN price on Pricing Maintenance). Closed orders and history keep the old codes, shown as broken rice.
The milling yield of broken rice is still kept per variety.

## Milling Order List in three groups (update 60)

The list has three tabs: **Queue / In process / Hold** (default), **Finished / Ready for delivery** and **Closed /
cancelled** (newest first). A searched or scanned order opens the tab it is in.

## Milling Analysis in AI (update 58)

Milling Analysis is part of the AI feature: the tile is in Automation (after AI), the screen is
/automation/ai/milling (old links redirect), the AI dashboard has a "Milling Analysis · last 30 days" card (yield
gauge vs expected, orders, waiting / milling time, holds, scanned, yield per variety), and the AI sees these
figures ("Ask AI about the milling" on the analysis screen). Rights stay on the menu AD_MILLANALYSIS.

## Customer Buying / Selling Order (update 56)

- Names from the customer's side: **Customer Selling Order** (BY: the customer sells, the mill buys - Prepared =
  Inventory Inbound) and **Customer Buying Order** (SL: the customer buys, the mill sells).
- A Customer Buying Order can be **linked with a milling order #** of the same customer: on Customer Service (the
  field under the table, type or scan the label) or later with "Link milling order" on the list. The badge
  "Milling #..." opens that milling order. It is only a link: the milling order closes on its own.
- **Inventory Outbound**: Prepared of a Customer Buying Order opens a pop-up - the free stock, take from a location or
  oldest first; the goods are kept for the order (Inventory List) until Closed; Cancelled puts them back.
- Milling Order List: search by customer name (also without spaces / with other tone marks, names offered while
  typing), code or phone.
- A Finished take-away changed to **Delivery** opens the delivery pop-up (address, distance, arrival time): the order
  goes to Ready for Delivery and the customer gets the LINE message with the time.

## Back to the milling order (update 55)

When the take-home selling order of a milling order (bran / broken rice) is closed or cancelled on the
Buying/Selling Order List, a pop-up asks "Back to the milling order now, to close it?" and opens the Milling Order
List on that order. The "From Milling #..." badge on a buying/selling card is also a link to its milling order.

## The day in the LINE messages (update 54)

The order messages have a new parameter **{day}**: วันนี้ / พรุ่งนี้ / เมื่อวาน, otherwise วันที่ DD/MM/YYYY (the day of
the time in the message, compared with the day the message is sent). The default texts use it ("...โดยประมาณพรุ่งนี้
เวลา 10:00"); saved texts that were still the old default are moved to the new one by start.sh; your own texts are
kept - add {day} on LINE Message Maintenance. Drop-downs that ask first (Close, Cancel, Hold ...) let the iPad
drop-down close before the pop-up opens, and scripts / styles are re-checked by the browser after every update.

## The whole card on LINE (update 52)

Print QR (queue) and Print QR card (customer) now send the **whole card** to the customer's LINE as a LINE Flex
Message, not only the QR picture: the customer card with the logo, AOI Labs, "โรงสีข้าว · บัตรลูกค้า", name,
customer code, mobile, the note and the QR; the queue card with MILLING + queue number, address, customer, payment,
receive time, the QR, the order code, phone / e-mail and the date. LINE draws the card (Thai text always right) and
fetches the logo and the QR from the public https address (as before: AOI_PUBLIC_URL or the browser address; the
Codespace port must be Public). The message text is the notification preview. Without an https address the text is
sent alone, as before.

## Mill selector turns by itself (update 51)

ON for the main motor the mill selector does not point at now **turns the selector first** (when the selector has
its own device: remote control, key, online), then the motor starts: the Start waits (AFTER_CMD) until the selector
reports it is there, and fails if the selector fails. A selector without a device must still be switched by hand first.
Only while no main motor runs. `bash start.sh sim` now also simulates the selector (it takes about 4 seconds to
turn). A command a gateway took but never answered (gateway stopped) fails after 5 minutes, so it no longer blocks
the selector; the inverter's live value wins over an old "running" status. On the SCADA the arrow of main motor 2
ends in packing (it looked like it went to the AMR).

## AI dashboard and Price analysis (update 50)

**Automation > AI** is a dashboard: a KPI strip (server, queue, paddy expected in 7 days, price signals, alerts, API
calls), gauges and a 24-hour line for disk / memory / CPU, API traffic bars and calls per hour, LINE status, device
tiles, the forecast chart, price-signal cards with sparklines, price-advice cards and the supply index.

**Automation > AI > Price analysis** puts every price source of a product on one chart (THB/kg):
NABC farm prices, the rice millers' report, the **rice exporters' weekly FOB prices**
(`AOI_EXPORT_URL`, default http://www.thairiceexporters.or.th/price.htm, US$/ton x the exchange rate), the applied
market price and your own buying / selling prices. On the base source it works out, like a stock chart: moving
averages (7 / 30 days), Bollinger bands, RSI 14, support / resistance, golden / death crosses (B / S on the chart), a
score (strong buy .. strong sell) with the reasons, the buy point and sell point, and for the stock on hand the cost
(average buying price of 90 days, or the price you type), profit / loss, **take profit** and **stop loss** (8 % / 5 %,
set on the screen). "Ask AI" explains it (the figures are part of what the AI sees). The exporters' page has no API:
the server reads the table every day with the other market prices; "Import" takes a saved copy of the page (HTML or
PDF) if the page cannot be downloaded. Set the exchange rate (THB per US$) once on Settings (or `AOI_USD_THB`).
Signals are statistics on past prices, not a promise: prices still change only on Pricing Maintenance.

## API, LINE and hardware monitoring (update 49)

The Server health card also shows:

- **API (last hour)** per area - app screens, device API (machines), LINE webhook, public (TV, QR pictures): calls,
  4xx, 5xx, slow (> 2 s), average / max ms, and device calls with a wrong key. Counted per hour in `AOITS_MONITOR`
  (3 days). Findings: server errors 1 / 10 per hour, slow 10 / 50, wrong device keys 20 / 200.
- **LINE interface**: token and channel secret set, the token checked with LINE every 30 minutes (refused = critical),
  the last call from LINE, the last message sent, messages refused / waiting in 24 h.
- **Hardware devices**: every machine in use with remote control and every meter with a key - online / offline / no
  key, live values, last report, failed or expired commands and faults in 24 h (warning when any).

The same findings give the red "!" on the menu and are part of what the AI sees.

## Customer Registration

- The name must not be registered yet (case and extra spaces ignored). The screen checks it before saving and
  shows a pop-up with the details to confirm; the server checks again.
- After a new registration the system asks whether to queue the customer for milling now.
- **Delete**: refused while the customer has an open order. With no orders the record is removed; with past orders
  it is hidden (`CUST_STATUS` D) so the accounts and the analysis keep it, the LINE link is cleared and the name can
  be registered again.

Administration pages with three or more sections have a **quick jump** bar at the top (sticky, highlights the
section on screen, "Top" goes back up).

## Investment & Break-even (draft, update 27)

Financial > **Investment & Break-even** (FN_INVEST, `/finance/investment`), per factory, three tabs:

- **Project investment**: project settings (loan amount, interest %, years; business mix = % of paddy milled as a
  service, % of the service by-products the mill keeps, variety for the per-kg figures) and the investment items
  (category, amount, purchase date, useful life, salvage value, own funds or loan). Straight-line depreciation; land
  and working capital are not depreciated. Loan repayment table (equal yearly payments). "Load sample project" fills
  a factory that has no items, for review.
- **Budget**: yearly budget per category (income; expense fixed or variable), "Fill labour & electricity from
  Milling Maintenance", "Copy from last year", and budget vs actual per category and per month. Actual figures come
  from Account: milling orders = milling fee, selling orders = sales, buying orders = purchase, `EL-` postings =
  electricity; manual entries now have an optional budget category (`ACC_CAT`).
- **Break-even point**: fixed cost per month (depreciation + year-1 loan interest + fixed budget lines / 12),
  revenue and variable cost per kg paddy (from the profit check on Pricing), contribution per kg, break-even kg per
  month and per day, capacity needed, margin of safety, profit per month/year, ROI, payback and a 10-year cash flow;
  chart of revenue against total cost with hover values.

## Market price history

Financial > Pricing Maintenance has two tabs: **Price Maintenance** (the profit check and the SKU prices) and
**Market Price** (the market sources, the mapping and a graph of every SKU's market price over 3, 6, 12 or 36
months, with SKU selection, hover/tap values and a summary table). The NABC API keeps daily prices from January
2025: "Download price history" (and the daily job, once per product) reads all of them. Association report SKUs use
the reports kept by the system, plus the older reports its page still lists.

## Main milling motor: ON/OFF, speed and live values (updates 29-30)

Hardware Control shows the **main milling motor** first (F1-MOTOR / F2-MOTOR: Mitsubishi SUPER LINE SF-J, 20 HP / 15 kW,
4 pole, 380 V 50 Hz, 30 A, 1450 rpm): **ON / OFF**, **speed** (30-50 Hz, slider + number; ON starts at the last speed
set), **Reset fault**, and the inverter's live values every 2 seconds (output Hz, current A, rpm, speed set, fault,
current above the rated 30 A) with two small charts of the last 10 minutes. The mill never runs in reverse: there is
no reverse command, and the gateway never writes the reverse-run signal. The sample machines (husker, whitener ...)
are kept but "not in use".

**Test without hardware (virtual inverter):**
1. Start the app (`bash start.sh`), open Hardware Control > Machine setup > **New key** for F2-MOTOR and copy the key.
2. In a second terminal:
   `python3 scripts/aoi_gateway.py --machine F2-MOTOR --key <key> --simulate`
   (the gateway starts the virtual inverter `scripts/vfd_sim.py` and links it to the app; in Codespaces the app is
   at http://127.0.0.1:5000, the default).
3. On Hardware Control: ON, change the speed, OFF. The motor ramps up and down in 5 seconds.
4. Type in the gateway terminal to test the alarms: `load 120` (motor overload, E.THM after a while), `jam`
   (overcurrent trip E.OC2), `clear`, `fault`, `status`. Then **Reset fault** on the screen.
5. Safety checks: stop the app while the motor runs - the gateway stops the motor after 30 seconds; stop the
   gateway - the virtual inverter stops by itself after 10 seconds without communication (E.SER).

**All motors at once (update 45) - no keys to copy:** `bash start.sh sim` starts the app AND simulates every machine in
use with remote control (main motor 1 and 2): `scripts/sim_all.py` gives each a device key straight in the database
(kept in `.gateways.local.json`, private, not in git, reused next time) and runs one gateway for all. Or, with the app
already running: `python3 scripts/sim_all.py` in a second terminal. Test commands there: `1 load 120`, `2 jam`,
`1 clear`, `2 fault`, `status`. A machine whose key belongs to a real device that is online is left alone. (Needs
MongoDB - not `start.sh demo`.)

**One gateway for several machines:** `python3 scripts/aoi_gateway.py --config pi.json` - one Raspberry Pi, both
inverters on the same RS-485 bus with station numbers 1 and 2. `python3 scripts/sim_all.py --export pi.json` writes that
file with the keys; copy it to the Pi (keep it private), check the url, port and station numbers.

**Later, the real inverter:** the same gateway on a Raspberry Pi with a USB-RS485 adapter:
`pip install pyserial` then `--modbus rtu:/dev/ttyUSB0:19200:E:1` instead of `--simulate`. The register map (REG in
`scripts/aoi_gateway.py`) follows the Mitsubishi Modbus map: check it against the inverter's manual, and set the
inverter for Modbus RTU, its station number, reverse rotation prevention (Pr.78 = 1) and a communication check time.
The emergency stop, the motor overload relay and the local/remote switch stay hard-wired; the app never replaces them.
Device API for any other device: `POST /api/v1/machines/<id>/telemetry` (see `app/controllers/device_api.py`).

## Two main motors and the mill selector (update 43)

Main motor 1 and main motor 2 are both in use, with the **mill selector** (ชุดสลับเครื่องสี, `F?-SELECT`) between them:
inbound -> selector -> motor 1 or motor 2 -> packing. **Only one main motor runs at a time**: ON is refused while the
other one runs (or is starting), and only the motor the selector points at can start. The selector can be switched
only while both motors are stopped - on the SCADA (tap the selector: "Switch to ...") or on Hardware Control. Without
its own device it is switched **by hand**: move the mechanical switch, then confirm in the app. With a device later
(remote control + key), the app sends `SELECT` (value = the motor's machine ID) and the position changes when the device
confirms; the device can also report its sensor as `"position": "F2-MOTOR2"` in the telemetry. The motors have no day /
night shift any more. Keep an electrical interlock in the panel too: the app's rule cannot stop a start at the inverter.
Hardware Control has quick links (each machine, commands, history, Machine setup) like the Administration pages.

## SCADA and the plant plan (updates 31, 34)

Update 34: the control-room design Tim approved (deep navy circuit-board background, glowing machine tiles on lit platforms,
glowing flow links, details panel with a speed gauge, monitors for output Hz, motor current and alarms). Fonts Chakra Petch /
IBM Plex Sans Thai come from Google Fonts (allowed in the security policy); without internet the system font is used.
Packing now feeds the AMR (Tim's updated plan).


Automation > **SCADA** (`/automation/scada`, Admin / Manager / Operator) draws the plant as Tim planned it, per factory:
Silo 1-2 -> Conveyor in 1-2 -> Milling inbound (queue) <- Sorting; Milling inbound -> Main motor 1 (day shift) and
Main motor 2 (night shift) -> Packing; Inventory storage/retrieval (AMR). Only **main motor 1 is in use**; the others are
planned and drawn dashed ("Disabled"). It refreshes every 2 seconds: state of each machine (colour only for state:
green running, red fault blinking, grey idle / no data), arrows that move where material flows, the main motor's Hz,
A and rpm with a speed bar, the current shift (day 06:00-18:00), plant figures (machines running, main motor, queue,
paddy today, energy today, alarms) and the alarms of the last 24 hours. Tap a machine for details; machines in use
can be switched ON/OFF, set to a speed or reset there. "Full screen" for the TV in the control room.
Each machine's place (SCADA column 0-5, row 0-3), the machines it feeds and its shift are set in Hardware Control >
Machine setup; turning on "In use" there adds it to Hardware Control. The sample machines of update 9 (husker,
whitener, polisher, grader) are removed when nobody set them up.


**Full screen (update 40):** the ON / OFF confirmation and the camera scanner now show inside full screen, and ON, OFF,
speed and Reset are sent without reloading the page, so the SCADA stays in full screen; a short message at the top shows
the answer.

## Automation and devices

The Automation screens work without any hardware: operators set a machine's status by hand and type
meter readings. To connect real machines (a PLC or a small computer next to the machine) and smart
meters later, they talk to the **device API** with a key issued on the screen (shown once, stored hashed):

| Call | Purpose |
|---|---|
| `POST /api/v1/machines/<ID>/status` `{"status": "R"}` | report the status (R running, I idle, F fault, M maintenance, O offline) |
| `GET /api/v1/machines/<ID>/commands` | fetch start/stop work (call every few seconds; it also marks the device as connected) |
| `POST /api/v1/commands/<CMD_ID>/ack` `{"ok": true}` | report the command result |
| `POST /api/v1/meters/<ID>/readings` `{"kwh": 12345.6}` | send a meter reading (cumulative kWh) |

Every call sends the header `X-Device-Key: <key>`. Try it without hardware:

```bash
python3 scripts/device_sim.py machine F2-HUSK <key>      # a pretend machine: reports status, carries out start/stop
python3 scripts/device_sim.py meter F2-MAIN <key>        # a pretend smart meter: a reading every 30 seconds
```

Start/stop is only offered for a machine with *Allow remote start/stop* ticked, a key, and a device
that called in during the last 5 minutes; it needs the Approve right and asks for confirmation.
A command not picked up within 2 minutes expires and never runs late. The device itself must keep
its own safety interlocks and emergency stop: the system only asks it to start or stop.

**Ask the system** (AI screen) needs an AI service key: put `AOI_AI_API_KEY=...` in `.env` and restart.
Only totals of the branch are sent (no customer names or phone numbers); each user can ask 20
questions an hour. Forecasts, price advice and alerts work without a key: they are worked out from
the branch's own data.

## Before real use

- Change every sample password, and set `AOI_SEED_PASSWORD` before seeding a new database.
- Give the User Maintenance and Role Authorization rights to administrators only: whoever can edit
  roles can grant any permission.
- Set `AOI_COOKIE_SECURE=1` when the site is served over HTTPS only, and keep `.env` out of Git.
- Run it behind a production web server (for example `gunicorn`), not `python run.py`.
- Set the real electricity rate on Electricity (the 4.5 THB/kWh in new databases is a sample), and keep
  `AOI_AI_API_KEY` secret (only in `.env`, never in Git or in the chat).
- Put the mill's own address, phone and email for the label in `.env`
  (`AOI_LABEL_ADDRESS_EN`, `AOI_LABEL_ADDRESS_TH`, `AOI_LABEL_TEL`, `AOI_LABEL_EMAIL`).

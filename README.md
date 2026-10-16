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
  Prepared asks the kg received per line, one location. Earlier orders (one per SKU, still Open) are merged once by
  `start.sh`.
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

**Later, the real inverter:** the same gateway on a Raspberry Pi with a USB-RS485 adapter:
`pip install pyserial` then `--modbus rtu:/dev/ttyUSB0:19200:E:1` instead of `--simulate`. The register map (REG in
`scripts/aoi_gateway.py`) follows the Mitsubishi Modbus map: check it against the inverter's manual, and set the
inverter for Modbus RTU, its station number, reverse rotation prevention (Pr.78 = 1) and a communication check time.
The emergency stop, the motor overload relay and the local/remote switch stay hard-wired; the app never replaces them.
Device API for any other device: `POST /api/v1/machines/<id>/telemetry` (see `app/controllers/device_api.py`).

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

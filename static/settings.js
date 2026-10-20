/*
 * Settings additions for Clip Pirate.
 *
 * Loaded after the main script in index.html:
 *     <script src="/app/settings.js"></script>
 *
 * Adds, without touching the existing code:
 *   1. Billing tab (renewal date, payment history)
 *   2. Change password + sign out of all devices (Security tab)
 *   3. Editable name (Profile tab)
 *   4. Default narration mode (Preferences tab)
 */
(function () {
    "use strict";

    var STYLE_ID = "settingsExtraStyle";

    var CSS = "\
.sx-form{display:grid;gap:10px;max-width:360px;width:100%}\
.sx-form label{font-size:12px;color:var(--muted)}\
.sx-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}\
.sx-msg{font-size:13px;min-height:18px}\
.sx-msg.ok{color:var(--success)}\
.sx-msg.err{color:var(--danger)}\
.sx-edit{display:flex;flex-direction:column;gap:8px;min-width:220px}\
.sx-edit.hidden,.sx-link.hidden{display:none}\
.sx-link{background:none;border:0;color:var(--primary);font-weight:600;cursor:pointer;padding:0;font-size:13px}\
.sx-danger{border-color:rgba(239,91,107,.5)!important;color:var(--danger)!important}\
.sx-table-wrap{overflow-x:auto;margin-top:6px}\
.sx-table{width:100%;border-collapse:collapse;font-size:13px}\
.sx-table th{color:var(--muted);font-weight:500;text-align:left;padding:0 10px 10px 0;font-size:12px}\
.sx-table td{padding:11px 10px 11px 0;border-top:1px solid var(--border);white-space:nowrap}\
.sx-empty{color:var(--muted);font-size:13px;padding:14px 0}\
.sx-h{font-size:15px;margin:22px 0 4px}\
.sx-dot{display:inline-block;min-width:18px;height:18px;line-height:18px;border-radius:9px;background:var(--danger);color:#fff;font-size:11px;font-weight:700;text-align:center;margin-left:8px;padding:0 5px;vertical-align:middle}\
.sx-dot.hidden{display:none}\
.sx-top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:6px 0 14px}\
.sx-conv{display:block;width:100%;text-align:left;background:var(--panel-2);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin-bottom:10px;color:var(--text);cursor:pointer}\
.sx-conv:hover{border-color:var(--primary)}\
.sx-conv-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:6px}\
.sx-conv-subject{font-weight:700;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
.sx-conv-prev{color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
.sx-conv-when{color:var(--muted);font-size:11px;margin-top:6px}\
.sx-chip{font-size:11px;padding:4px 9px;border-radius:20px;background:rgba(124,92,255,.12);color:#b7aaff;white-space:nowrap}\
.sx-chip.pending{background:rgba(49,196,141,.10);color:#63dcae}\
.sx-chip.resolved,.sx-chip.closed{background:rgba(146,153,168,.12);color:#9299a8}\
.sx-msgs{display:flex;flex-direction:column;gap:10px;max-height:420px;overflow-y:auto;padding:4px 2px 4px 0;margin:10px 0 14px}\
.sx-bubble{max-width:82%;padding:10px 13px;border-radius:12px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}\
.sx-bubble.user{align-self:flex-end;background:rgba(124,92,255,.18);border:1px solid rgba(124,92,255,.35)}\
.sx-bubble.admin{align-self:flex-start;background:var(--panel-2);border:1px solid var(--border)}\
.sx-bubble small{display:block;color:var(--muted);font-size:11px;margin-top:6px}\
.sx-chat-form{display:grid;gap:10px}\
.sx-chat-form textarea{min-height:90px}\
";

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function $(id) { return document.getElementById(id); }

    function post(url, payload) {
        return window.api(url, { method: "POST", body: JSON.stringify(payload || {}) });
    }

    function say(node, text, ok) {
        if (!node) { return; }
        node.textContent = text || "";
        node.className = "sx-msg" + (text ? (ok ? " ok" : " err") : "");
    }

    function toast(message) {
        if (typeof window.showToast === "function") { window.showToast(message); }
    }

    function addStyle() {
        if ($(STYLE_ID)) { return; }
        var style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    function row(title, text, controlHtml) {
        return '<div class="setting-row"><div class="setting-info"><h3>' + esc(title) + '</h3><p>' +
            esc(text) + '</p></div>' + (controlHtml || "") + '</div>';
    }

    /* ----------------------------------------------------
       1. BILLING TAB
    ---------------------------------------------------- */

    function money(amount, currency) {
        var n = Number(amount);
        if (isNaN(n)) { return String(amount); }
        try {
            return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD" }).format(n);
        } catch (e) {
            return (currency || "") + " " + n.toFixed(2);
        }
    }

    function dateText(iso) {
        var d = new Date(iso);
        if (isNaN(d.getTime())) { return "—"; }
        return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    }

    function statusClass(status) {
        status = String(status || "").toLowerCase();
        if (status === "paid") { return "completed"; }
        if (status === "pending") { return "processing"; }
        return "failed";
    }

    function statusLabel(status) {
        status = String(status || "").toLowerCase();
        return status === "paid" ? "Paid" : status === "pending" ? "Pending" : status === "failed" ? "Failed" : status;
    }

    function addBillingTab() {
        var nav = document.querySelector(".settings-nav");
        var card = document.querySelector("#page-settings .form-card");
        if (!nav || !card || $("settings-billing")) { return; }

        var accountButton = Array.prototype.find.call(nav.querySelectorAll("button"), function (b) {
            return /showSettingsPanel\('account'/.test(b.getAttribute("onclick") || "");
        });

        var button = document.createElement("button");
        button.textContent = "Billing";
        button.setAttribute("onclick", "showSettingsPanel('billing', this)");

        if (accountButton && accountButton.nextSibling) {
            nav.insertBefore(button, accountButton.nextSibling);
        } else {
            nav.appendChild(button);
        }

        var panel = document.createElement("div");
        panel.id = "settings-billing";
        panel.className = "settings-panel";
        panel.innerHTML = "<h2>Billing</h2><p>Your plan, renewal date and payments.</p>" +
            '<div id="billingBody"><div class="sx-empty">Loading billing...</div></div>' +
            '<div id="billingPlans"></div>';
        card.appendChild(panel);
        movePlansToBilling();
    }

    /* Subscription plans live in Billing now: move the existing heading,
       currency picker and plans grid out of the Account panel. */
    function movePlansToBilling() {
        var grid = $("plansGrid");
        var target = $("billingPlans");
        if (!grid || !target || target.contains(grid)) { return; }
        var head = grid.previousElementSibling;
        if (head && head.querySelector && head.querySelector("#currencyPicker")) {
            head.style.marginTop = "28px";
            target.appendChild(head);
        }
        target.appendChild(grid);
        var acct = $("settings-account");
        if (acct) {
            var p = acct.querySelector("p");
            if (p) { p.textContent = "Your current plan and credits. Manage plans and payments in Billing."; }
        }
    }

    async function loadBilling() {
        var body = $("billingBody");
        if (!body) { return; }

        try {
            var d = await window.api("/settings/billing");
            var free = d.plan === "free";

            var renew;
            if (free) {
                renew = "Free plan. No renewal needed.";
            } else if (d.expires_at) {
                renew = (d.days_left === 0 ? "Ends today" : "Active until " + dateText(d.expires_at) +
                    " (" + d.days_left + (d.days_left === 1 ? " day" : " days") + " left)");
            } else {
                renew = "Active";
            }

            var html =
                row("Current plan", d.credits + " credits available.", '<div class="setting-control"><strong>' + esc(d.plan_name) + '</strong></div>') +
                row(free ? "Upgrade" : "Renewal", renew,
                    '<button class="create-btn" id="billingPlansBtn">' + (free ? "See plans" : "Renew or change plan") + '</button>');

            html += '<h3 class="sx-h">Payment history</h3>';

            if (!d.payments || !d.payments.length) {
                html += '<div class="sx-empty">No payments yet.</div>';
            } else {
                html += '<div class="sx-table-wrap"><table class="sx-table"><thead><tr><th>Date</th><th>Plan</th><th>Amount</th>' +
                    '<th>Method</th><th>Status</th><th>Reference</th></tr></thead><tbody>' +
                    d.payments.map(function (p) {
                        return "<tr><td>" + esc(dateText(p.created_at)) + "</td><td>" + esc(p.plan) + "</td><td>" +
                            esc(money(p.amount, p.currency)) + "</td><td>" + esc(p.provider) + '</td><td><span class="status ' +
                            statusClass(p.status) + '">' + esc(statusLabel(p.status)) + "</span></td><td>" +
                            esc(p.reference || "—") + "</td></tr>";
                    }).join("") + "</tbody></table></div>";
            }

            body.innerHTML = html;

            var btn = $("billingPlansBtn");
            if (btn) {
                btn.addEventListener("click", function () {
                    var plans = $("billingPlans");
                    if (plans && plans.scrollIntoView) { plans.scrollIntoView({ behavior: "smooth", block: "start" }); }
                });
            }
        } catch (error) {
            body.innerHTML = '<div class="sx-empty">' + esc(error.message || "Could not load billing.") + "</div>";
        }
    }

    /* ----------------------------------------------------
       2. SECURITY: CHANGE PASSWORD + SIGN OUT EVERYWHERE
    ---------------------------------------------------- */

    function addSecurity() {
        var panel = $("settings-security");
        if (!panel || $("sxPasswordForm")) { return; }

        var wrap = document.createElement("div");
        wrap.innerHTML =
            row("Change password", "Choose a new password. Other devices will be signed out.",
                '<form id="sxPasswordForm" class="sx-form" autocomplete="off">' +
                '<div><label for="sxCurrentPw">Current password</label><input id="sxCurrentPw" type="password" autocomplete="current-password" required></div>' +
                '<div><label for="sxNewPw">New password (at least 8 characters)</label><input id="sxNewPw" type="password" autocomplete="new-password" minlength="8" required></div>' +
                '<div><label for="sxConfirmPw">Confirm new password</label><input id="sxConfirmPw" type="password" autocomplete="new-password" minlength="8" required></div>' +
                '<div class="sx-actions"><button class="create-btn" type="submit" id="sxPwBtn">Update password</button></div>' +
                '<div class="sx-msg" id="sxPwMsg" role="status"></div></form>') +
            row("Sign out of all devices", "Ends every session, including this one. Use this if you lost a device.",
                '<button class="secondary-btn sx-danger" id="sxSignoutAll" type="button">Sign out everywhere</button>');

        while (wrap.firstChild) { panel.appendChild(wrap.firstChild); }

        $("sxPasswordForm").addEventListener("submit", async function (event) {
            event.preventDefault();

            var msg = $("sxPwMsg");
            var current = $("sxCurrentPw").value;
            var next = $("sxNewPw").value;
            var confirmValue = $("sxConfirmPw").value;

            say(msg, "");

            if (next !== confirmValue) { say(msg, "The new passwords do not match.", false); return; }
            if (next.length < 8) { say(msg, "Your new password must be at least 8 characters.", false); return; }

            var button = $("sxPwBtn");
            button.disabled = true;

            try {
                var result = await post("/settings/password", { current_password: current, new_password: next });
                $("sxPasswordForm").reset();
                var others = result && result.signed_out_other_devices;
                say(msg, "Password updated." + (others ? " " + others + (others === 1 ? " other device was" : " other devices were") + " signed out." : ""), true);
                toast("Password updated.");
            } catch (error) {
                say(msg, error.message || "Could not update your password.", false);
            } finally {
                button.disabled = false;
            }
        });

        $("sxSignoutAll").addEventListener("click", async function () {
            if (!window.confirm("Sign out of all devices, including this one?")) { return; }
            try {
                await post("/settings/signout-all");
            } catch (error) {
                toast(error.message || "Could not sign out everywhere.");
                return;
            }
            if (typeof window.logout === "function") { window.logout(); }
        });
    }

    /* ----------------------------------------------------
       3. PROFILE: EDIT NAME
    ---------------------------------------------------- */

    function addNameEditor() {
        var display = $("settingsName");
        if (!display || $("sxNameEdit")) { return; }

        var rowNode = display.closest(".setting-row");
        if (!rowNode) { return; }

        var edit = document.createElement("div");
        edit.id = "sxNameEdit";
        edit.className = "sx-edit hidden";
        edit.innerHTML = '<input id="sxNameInput" type="text" maxlength="60" aria-label="Name">' +
            '<div class="sx-actions"><button class="create-btn" type="button" id="sxNameSave">Save</button>' +
            '<button class="secondary-btn" type="button" id="sxNameCancel">Cancel</button></div>' +
            '<div class="sx-msg" id="sxNameMsg" role="status"></div>';

        var link = document.createElement("button");
        link.type = "button";
        link.className = "sx-link";
        link.id = "sxNameLink";
        link.textContent = "Edit";

        display.parentNode.insertBefore(link, display.nextSibling);
        rowNode.appendChild(edit);

        var close = function () {
            edit.classList.add("hidden");
            display.classList.remove("hidden");
            link.classList.remove("hidden");
            say($("sxNameMsg"), "");
        };

        link.addEventListener("click", function () {
            $("sxNameInput").value = (typeof currentUser !== "undefined" && currentUser && currentUser.name) || display.textContent || "";
            display.classList.add("hidden");
            link.classList.add("hidden");
            edit.classList.remove("hidden");
            $("sxNameInput").focus();
        });

        $("sxNameCancel").addEventListener("click", close);

        $("sxNameSave").addEventListener("click", async function () {
            var button = this;
            var name = $("sxNameInput").value;
            button.disabled = true;
            try {
                var result = await post("/settings/profile", { name: name });
                if (typeof currentUser !== "undefined" && currentUser) { currentUser.name = result.name; }
                if (typeof window.updateUserInterface === "function") { window.updateUserInterface(); }
                else { display.textContent = result.name; }
                close();
                toast("Name updated.");
            } catch (error) {
                say($("sxNameMsg"), error.message || "Could not update your name.", false);
            } finally {
                button.disabled = false;
            }
        });

        $("sxNameInput").addEventListener("keydown", function (event) {
            if (event.key === "Enter") { event.preventDefault(); $("sxNameSave").click(); }
            if (event.key === "Escape") { close(); }
        });
    }

    /* ----------------------------------------------------
       4. PREFERENCES: DEFAULT NARRATION MODE
    ---------------------------------------------------- */

    var PREF_KEY = "clipPiratePreferences";

    function readPrefs() {
        try { return JSON.parse(localStorage.getItem(PREF_KEY)) || {}; } catch (e) { return {}; }
    }

    function writePrefs(prefs) {
        try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ }
    }

    function isFreePlan() {
        var plan = String((typeof currentUser !== "undefined" && currentUser && (currentUser.plan || currentUser.plan_name)) || "free").toLowerCase();
        return plan === "free";
    }

    function syncNarrationSelect() {
        var select = $("prefNarration");
        if (!select) { return; }

        var locked = isFreePlan();
        var dialogue = Array.prototype.find.call(select.options, function (o) { return o.value === "dialogue"; });

        if (dialogue) {
            dialogue.disabled = locked;
            dialogue.textContent = locked ? "🔒 Dialogue (Starter+)" : "Dialogue (Host + Guest)";
        }

        var saved = readPrefs().narration === "dialogue" && !locked ? "dialogue" : "solo";
        select.value = saved;
    }

    function addNarrationPreference() {
        var panel = $("settings-preferences");
        if (!panel || $("prefNarration")) { return; }

        var saveButton = panel.querySelector(".save-settings");

        var holder = document.createElement("div");
        holder.innerHTML = row("Default Narration Mode", "Solo uses one narrator. Dialogue uses a Host and a Guest (Starter and above).",
            '<div class="setting-control"><select id="prefNarration"><option value="solo">Solo</option>' +
            '<option value="dialogue">Dialogue (Host + Guest)</option></select></div>');

        if (saveButton) {
            panel.insertBefore(holder.firstChild, saveButton);
        } else {
            panel.appendChild(holder.firstChild);
        }

        syncNarrationSelect();
    }

    /* ----------------------------------------------------
       5. EMAIL REMINDERS SWITCH (saves immediately)
    ---------------------------------------------------- */

    function addReminderSwitch() {
        var panel = $("settings-preferences");
        if (!panel || $("prefReminders")) { return; }

        var saveButton = panel.querySelector(".save-settings");

        var holder = document.createElement("div");
        holder.innerHTML = row("Email Reminders", "Get an email when your paid plan is about to end or your credits run low.",
            '<div class="setting-control"><label class="checkbox-row"><input id="prefReminders" type="checkbox" checked> Enabled</label>' +
            '<div class="sx-msg" id="prefRemindersMsg" role="status"></div></div>');

        if (saveButton) {
            panel.insertBefore(holder.firstChild, saveButton);
        } else {
            panel.appendChild(holder.firstChild);
        }

        $("prefReminders").addEventListener("change", async function () {
            var box = $("prefReminders");
            var msg = $("prefRemindersMsg");
            var wanted = box.checked;
            box.disabled = true;
            try {
                await post("/settings/reminders", { enabled: wanted });
                say(msg, wanted ? "Reminders on." : "Reminders off.", true);
            } catch (error) {
                box.checked = !wanted;
                say(msg, error.message || "Could not save that.", false);
            }
            box.disabled = false;
        });
    }

    async function loadReminderSwitch() {
        var box = $("prefReminders");
        if (!box) { return; }
        try {
            var data = await window.api("/settings/reminders");
            box.checked = !!data.enabled;
        } catch (error) { /* leave the default */ }
    }

    /* ----------------------------------------------------
       6. YOUR DATA: DOWNLOAD + DELETE ACCOUNT
    ---------------------------------------------------- */

    function addDataSection() {
        var panel = $("settings-account");
        if (!panel || $("sxExport")) { return; }

        var wrap = document.createElement("div");
        wrap.innerHTML =
            '<h3 class="sx-h" style="margin-top:36px">Your data</h3>' +
            row("Download my data", "A JSON file with your account, payments and every project's topic, script and settings. Video files are not included.",
                '<div class="sx-edit"><button class="secondary-btn" id="sxExport" type="button">Download my data</button>' +
                '<div class="sx-msg" id="sxExportMsg" role="status"></div></div>') +
            row("Delete account", "Permanently deletes your account, projects and videos. This cannot be undone. Paid plans must have ended first.",
                '<div class="sx-edit"><button class="secondary-btn sx-danger" id="sxDeleteOpen" type="button">Delete account</button>' +
                '<form id="sxDeleteForm" class="sx-form hidden" autocomplete="off">' +
                '<div><label for="sxDelPw">Your password</label><input id="sxDelPw" type="password" autocomplete="current-password" required></div>' +
                '<div><label for="sxDelConfirm">Type DELETE to confirm</label><input id="sxDelConfirm" type="text" autocomplete="off" required></div>' +
                '<div class="sx-actions"><button class="secondary-btn sx-danger" type="submit" id="sxDelBtn">Delete my account forever</button>' +
                '<button class="sx-link" type="button" id="sxDelCancel">Cancel</button></div>' +
                '<div class="sx-msg" id="sxDelMsg" role="status"></div></form></div>');

        while (wrap.firstChild) { panel.appendChild(wrap.firstChild); }

        $("sxExport").addEventListener("click", async function () {
            var btn = $("sxExport");
            var msg = $("sxExportMsg");
            btn.disabled = true;
            say(msg, "Preparing your file...", true);
            try {
                var data = await window.api("/settings/export");
                var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                var link = document.createElement("a");
                link.href = URL.createObjectURL(blob);
                link.download = "clip-pirate-data-" + new Date().toISOString().slice(0, 10) + ".json";
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
                say(msg, "Downloaded.", true);
            } catch (error) {
                say(msg, error.message || "Could not prepare your data.", false);
            }
            btn.disabled = false;
        });

        var form = $("sxDeleteForm");
        var open = $("sxDeleteOpen");

        open.addEventListener("click", function () {
            form.classList.remove("hidden");
            open.classList.add("hidden");
            $("sxDelPw").focus();
        });

        $("sxDelCancel").addEventListener("click", function () {
            form.reset();
            say($("sxDelMsg"), "");
            form.classList.add("hidden");
            open.classList.remove("hidden");
        });

        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            var msg = $("sxDelMsg");
            var btn = $("sxDelBtn");
            btn.disabled = true;
            say(msg, "");
            try {
                await post("/settings/delete-account", {
                    password: $("sxDelPw").value,
                    confirm: $("sxDelConfirm").value.trim()
                });
                toast("Your account was deleted.");
                setTimeout(function () { window.location.href = "/"; }, 900);
            } catch (error) {
                say(msg, error.message || "Could not delete the account.", false);
                btn.disabled = false;
            }
        });
    }

    /* ----------------------------------------------------
       7. HELP: CHAT WITH SUPPORT
    ---------------------------------------------------- */

    var help = { view: "list", id: null, shownId: null, count: 0, timer: null };

    var STATUS_LABEL = { open: "Open", pending: "Replied", resolved: "Resolved", closed: "Closed" };

    function whenText(iso) {
        var d = new Date(iso);
        if (isNaN(d.getTime())) { return ""; }
        return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }

    function chip(status) {
        return '<span class="sx-chip ' + esc(status) + '">' + esc(STATUS_LABEL[status] || status) + '</span>';
    }

    function addHelpTab() {
        var nav = document.querySelector(".settings-nav");
        var card = document.querySelector("#page-settings .form-card");
        if (!nav || !card || $("settings-help")) { return; }

        var button = document.createElement("button");
        button.id = "sxHelpButton";
        button.innerHTML = 'Help <span class="sx-dot hidden" id="sxHelpDot">0</span>';
        button.setAttribute("onclick", "showSettingsPanel('help', this)");
        nav.appendChild(button);

        var panel = document.createElement("div");
        panel.id = "settings-help";
        panel.className = "settings-panel";
        panel.innerHTML = "<h2>Help</h2><p>Questions about your account, billing or videos? Message us here and we will reply in the app.</p>" +
            '<div id="helpBody"><div class="sx-empty">Loading...</div></div>';
        card.appendChild(panel);
    }

    function setUnread(count) {
        help.count = count;

        var dot = $("sxHelpDot");
        if (dot) {
            dot.textContent = count > 99 ? "99+" : String(count);
            dot.classList.toggle("hidden", !count);
        }

        // A small marker on the sidebar's Settings button too.
        var label = document.querySelector('.nav-button[data-page="settings"] .nav-label');
        if (label) {
            var side = $("sxSideDot");
            if (!side) {
                side = document.createElement("span");
                side.id = "sxSideDot";
                side.className = "sx-dot hidden";
                label.appendChild(side);
            }
            side.textContent = count > 99 ? "99+" : String(count);
            side.classList.toggle("hidden", !count);
        }
    }

    async function refreshUnread() {
        if (typeof currentUser === "undefined" || !currentUser || document.hidden) { return; }
        try {
            var data = await window.api("/support/unread");
            setUnread(Number(data.count) || 0);
        } catch (error) { /* stay quiet */ }
    }

    function helpActive() {
        var panel = $("settings-help");
        return !!panel && panel.classList.contains("active");
    }

    async function helpList() {
        help.view = "list";
        help.id = null;
        help.shownId = null;

        var body = $("helpBody");
        if (!body) { return; }

        try {
            var data = await window.api("/support/conversations");
            var items = data.conversations || [];

            var html = '<div class="sx-top"><h3 class="sx-h" style="margin:0">Your messages</h3>' +
                '<button class="create-btn" id="helpNewBtn" type="button">New message</button></div>';

            if (!items.length) {
                html += '<div class="sx-empty">No messages yet. Ask us anything about your account, billing or videos.</div>';
            } else {
                html += items.map(function (c) {
                    return '<button class="sx-conv" type="button" data-id="' + esc(c.id) + '">' +
                        '<div class="sx-conv-top"><span class="sx-conv-subject">' + esc(c.subject) +
                        (c.unread ? ' <span class="sx-dot">' + c.unread + '</span>' : "") + '</span>' + chip(c.status) + '</div>' +
                        '<div class="sx-conv-prev">' + (c.last_sender === "admin" ? "Support: " : "You: ") + esc(c.preview) + '</div>' +
                        '<div class="sx-conv-when">' + esc(whenText(c.last_at)) + '</div></button>';
                }).join("");
            }

            body.innerHTML = html;

            $("helpNewBtn").addEventListener("click", helpNew);
            Array.prototype.forEach.call(body.querySelectorAll(".sx-conv"), function (el) {
                el.addEventListener("click", function () { helpThread(el.getAttribute("data-id")); });
            });

            setUnread(items.reduce(function (sum, c) { return sum + (c.unread || 0); }, 0));
        } catch (error) {
            body.innerHTML = '<div class="sx-empty">' + esc(error.message || "Could not load your messages.") + '</div>';
        }
    }

    function helpNew() {
        help.view = "new";
        help.shownId = null;

        $("helpBody").innerHTML =
            '<button class="sx-link" id="helpBack" type="button">&larr; Back to messages</button>' +
            '<form id="helpNewForm" class="sx-chat-form" style="margin-top:14px;max-width:560px" autocomplete="off">' +
            '<div><label class="sx-lbl" for="helpSubject" style="font-size:12px;color:var(--muted)">Subject</label>' +
            '<input id="helpSubject" maxlength="80" placeholder="For example: I was charged but my plan did not change" required></div>' +
            '<div><label for="helpText" style="font-size:12px;color:var(--muted)">Message</label>' +
            '<textarea id="helpText" maxlength="2000" placeholder="Tell us what happened. Include the video topic or payment date if it helps." required></textarea></div>' +
            '<div class="sx-actions"><button class="create-btn" type="submit" id="helpSend">Send message</button></div>' +
            '<div class="sx-msg" id="helpMsg" role="status"></div></form>';

        $("helpBack").addEventListener("click", helpList);

        $("helpNewForm").addEventListener("submit", async function (event) {
            event.preventDefault();
            var btn = $("helpSend");
            btn.disabled = true;
            say($("helpMsg"), "");
            try {
                var result = await post("/support/conversations", {
                    subject: $("helpSubject").value,
                    message: $("helpText").value
                });
                toast("Message sent. We will reply here.");
                helpThread(result.id);
            } catch (error) {
                say($("helpMsg"), error.message || "Could not send your message.", false);
                btn.disabled = false;
            }
        });

        $("helpSubject").focus();
    }

    function drawMessages(messages) {
        var box = $("helpMsgs");
        if (!box) { return; }

        var stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
        var before = box.children.length;

        box.textContent = "";

        messages.forEach(function (m) {
            var bubble = document.createElement("div");
            bubble.className = "sx-bubble " + (m.from === "admin" ? "admin" : "user");
            bubble.textContent = m.message;

            var meta = document.createElement("small");
            meta.textContent = (m.from === "admin" ? "Clip Pirate support" : "You") + " · " + whenText(m.created_at);
            bubble.appendChild(meta);

            box.appendChild(bubble);
        });

        if (stick || !before) { box.scrollTop = box.scrollHeight; }
    }

    async function helpThread(id, quiet) {
        var body = $("helpBody");
        if (!body) { return; }

        help.view = "thread";
        help.id = id;

        try {
            var data = await window.api("/support/conversations/" + encodeURIComponent(id));

            if (help.shownId !== id) {
                help.shownId = id;
                var closed = data.status === "closed";

                body.innerHTML =
                    '<button class="sx-link" id="helpBack" type="button">&larr; Back to messages</button>' +
                    '<div class="sx-top" style="margin-top:12px"><h3 class="sx-h" style="margin:0" id="helpSubjectTitle"></h3><span id="helpStatus"></span></div>' +
                    '<div class="sx-msgs" id="helpMsgs"></div>' +
                    (closed
                        ? '<div class="sx-empty" id="helpClosed">This conversation is closed. Start a new message if you need more help.</div>'
                        : '<form id="helpReplyForm" class="sx-chat-form" autocomplete="off">' +
                          '<textarea id="helpReply" maxlength="2000" placeholder="Write a reply..." required></textarea>' +
                          '<div class="sx-actions"><button class="create-btn" type="submit" id="helpReplyBtn">Send</button></div>' +
                          '<div class="sx-msg" id="helpReplyMsg" role="status"></div></form>');

                $("helpBack").addEventListener("click", helpList);

                var form = $("helpReplyForm");
                if (form) {
                    form.addEventListener("submit", async function (event) {
                        event.preventDefault();
                        var btn = $("helpReplyBtn");
                        var text = $("helpReply");
                        btn.disabled = true;
                        say($("helpReplyMsg"), "");
                        try {
                            await post("/support/conversations/" + encodeURIComponent(id) + "/messages", { message: text.value });
                            text.value = "";
                            help.shownId = null;
                            await helpThread(id, true);
                        } catch (error) {
                            say($("helpReplyMsg"), error.message || "Could not send your reply.", false);
                        }
                        btn.disabled = false;
                    });
                }
            }

            $("helpSubjectTitle").textContent = data.subject;
            $("helpStatus").innerHTML = chip(data.status);
            drawMessages(data.messages || []);
            refreshUnread();
        } catch (error) {
            if (!quiet) {
                body.innerHTML = '<button class="sx-link" id="helpBack" type="button">&larr; Back to messages</button>' +
                    '<div class="sx-empty">' + esc(error.message || "Could not open this conversation.") + '</div>';
                $("helpBack").addEventListener("click", helpList);
            }
        }
    }

    function openHelp() {
        helpList();

        if (help.timer) { clearInterval(help.timer); }

        // New replies appear on their own while the Help tab is open.
        help.timer = setInterval(function () {
            if (!helpActive() || document.hidden) {
                if (!helpActive()) { clearInterval(help.timer); help.timer = null; }
                return;
            }
            if (help.view === "thread" && help.id) { helpThread(help.id, true); }
            else if (help.view === "list") { helpList(); }
        }, 15000);
    }

    function wrap(name, after, before) {
        var original = window[name];
        if (typeof original !== "function") { return; }

        window[name] = function () {
            var captured = before ? before() : undefined;
            var result = original.apply(this, arguments);
            try { after(captured); } catch (e) { console.error(e); }
            return result;
        };
    }

    function installPreferenceHooks() {
        wrap("loadPreferences", syncNarrationSelect);

        wrap("savePreferences",
            function (value) {
                var prefs = readPrefs();
                prefs.narration = value === "dialogue" && !isFreePlan() ? "dialogue" : "solo";
                writePrefs(prefs);
            },
            function () {
                var select = $("prefNarration");
                return select ? select.value : "solo";
            });

        wrap("loadPreferencesIntoCreateForm", function () {
            // A template that asks for a narration mode wins over the default.
            var templateWants = typeof activeTemplate !== "undefined" && activeTemplate && activeTemplate.narration_mode;
            var select = $("narrationMode");

            if (!select || templateWants || isFreePlan()) { return; }

            if (readPrefs().narration === "dialogue") {
                select.value = "dialogue";
                if (typeof window.applyNarrationAccess === "function") { window.applyNarrationAccess(); }
            }
        });
    }

    /* ----------------------------------------------------
       WIRING
    ---------------------------------------------------- */

    function init() {
        addStyle();
        addBillingTab();
        addSecurity();
        addNameEditor();
        addNarrationPreference();
        addReminderSwitch();
        addDataSection();
        addHelpTab();
        installPreferenceHooks();

        if (typeof window.showSettingsPanel === "function") {
            var base = window.showSettingsPanel;
            window.showSettingsPanel = function (name) {
                var result = base.apply(this, arguments);
                if (name === "billing") {
                    loadBilling();
                    if (typeof window.loadPlans === "function") { window.loadPlans(); }
                }
                if (name === "preferences") { loadReminderSwitch(); }
                if (name === "help") { openHelp(); }
                return result;
            };
        }
    }

    setTimeout(refreshUnread, 2500);
    setInterval(refreshUnread, 60000);

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

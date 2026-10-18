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
            '<div id="billingBody"><div class="sx-empty">Loading billing...</div></div>';
        card.appendChild(panel);
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
                    var nav = document.querySelector(".settings-nav");
                    var acct = Array.prototype.find.call(nav.querySelectorAll("button"), function (b) {
                        return /showSettingsPanel\('account'/.test(b.getAttribute("onclick") || "");
                    });
                    window.showSettingsPanel("account", acct);
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
        installPreferenceHooks();

        if (typeof window.showSettingsPanel === "function") {
            var base = window.showSettingsPanel;
            window.showSettingsPanel = function (name) {
                var result = base.apply(this, arguments);
                if (name === "billing") { loadBilling(); }
                return result;
            };
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

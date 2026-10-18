/*
 * Extra Analytics sections for Clip Pirate.
 *
 * Loaded after the main script in index.html:
 *     <script src="/app/analytics.js"></script>
 *
 * It wraps the existing loadAnalytics(), so the original cards keep working,
 * and adds credits, a 30-day chart, success rate, breakdowns and recent
 * activity underneath. Data comes from GET /analytics.
 */
(function () {
    "use strict";

    var STYLE_ID = "analyticsExtraStyle";
    var BOX_ID = "analyticsExtra";

    var CSS = "\
#analyticsExtra{margin-top:20px;display:grid;gap:20px;min-width:0;max-width:100%}\
#analyticsExtra>*{min-width:0}\
#analyticsExtra .ax-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}\
#analyticsExtra .ax-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}\
#analyticsExtra .ax-sub{color:var(--muted);font-size:12px;margin-top:6px}\
#analyticsExtra .ax-chart{display:flex;align-items:flex-end;gap:3px;height:140px;padding-top:8px}\
#analyticsExtra .ax-col{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:flex-end;height:100%}\
#analyticsExtra .ax-bar{background:var(--primary);border-radius:3px 3px 0 0;min-height:2px;opacity:.9}\
#analyticsExtra .ax-bar.zero{background:var(--border);opacity:1}\
#analyticsExtra .ax-col:hover .ax-bar{opacity:1;background:#9a82ff}\
#analyticsExtra .ax-axis{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:8px}\
#analyticsExtra .ax-row{margin-bottom:12px}\
#analyticsExtra .ax-row:last-child{margin-bottom:0}\
#analyticsExtra .ax-row-top{display:flex;justify-content:space-between;gap:10px;font-size:13px;margin-bottom:5px}\
#analyticsExtra .ax-row-top span:last-child{color:var(--muted)}\
#analyticsExtra .ax-track{height:7px;background:var(--panel-3,#1d212b);border-radius:99px;overflow:hidden}\
#analyticsExtra .ax-fill{height:100%;background:var(--primary);border-radius:99px}\
#analyticsExtra .ax-empty{color:var(--muted);font-size:13px;padding:6px 0}\
#analyticsExtra .ax-table{width:100%;border-collapse:collapse;font-size:13px}\
#analyticsExtra .ax-table th{color:var(--muted);font-weight:500;text-align:left;padding:0 8px 10px 0;font-size:12px}\
#analyticsExtra .ax-table td{padding:10px 8px 10px 0;border-top:1px solid var(--border);vertical-align:middle}\
#analyticsExtra .ax-topic{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
#analyticsExtra .ax-scroll{overflow-x:auto}\
@media (max-width:900px){#analyticsExtra .ax-grid{grid-template-columns:minmax(0,1fr)}#analyticsExtra .ax-two{grid-template-columns:minmax(0,1fr)}}\
";

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function shortDate(iso) {
        var d = new Date(iso + "T00:00:00Z");
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
    }

    function whenText(iso) {
        var d = new Date(iso);
        if (isNaN(d.getTime())) { return ""; }
        return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }

    function ensureBox() {
        if (!document.getElementById(STYLE_ID)) {
            var style = document.createElement("style");
            style.id = STYLE_ID;
            style.textContent = CSS;
            document.head.appendChild(style);
        }

        var box = document.getElementById(BOX_ID);

        if (!box) {
            var page = document.getElementById("page-analytics");
            if (!page) { return null; }
            box = document.createElement("div");
            box.id = BOX_ID;
            page.appendChild(box);
        }

        return box;
    }

    function card(title, value, note, valueStyle) {
        return '<div class="analytics-card"><h3>' + esc(title) + '</h3>' +
            '<div class="analytics-number"' + (valueStyle ? ' style="' + valueStyle + '"' : "") + '>' +
            esc(value) + '</div>' +
            (note ? '<div class="ax-sub">' + esc(note) + '</div>' : "") +
            '</div>';
    }

    function chart(series) {
        var max = 0;
        series.forEach(function (s) { if (s.count > max) { max = s.count; } });

        var cols = series.map(function (s) {
            var pct = max ? Math.max((s.count / max) * 100, s.count ? 8 : 0) : 0;
            var label = shortDate(s.date) + ": " + s.count + (s.count === 1 ? " video" : " videos");
            return '<div class="ax-col" title="' + esc(label) + '">' +
                '<div class="ax-bar' + (s.count ? "" : " zero") + '" style="height:' + (s.count ? pct : 2) + (s.count ? "%" : "px") + '"></div></div>';
        }).join("");

        return '<div class="ax-chart" role="img" aria-label="Videos per day">' + cols + '</div>' +
            '<div class="ax-axis"><span>' + esc(shortDate(series[0].date)) + '</span><span>' +
            esc(shortDate(series[series.length - 1].date)) + '</span></div>';
    }

    function breakdown(title, items) {
        var body;

        if (!items || !items.length) {
            body = '<div class="ax-empty">Nothing to show yet.</div>';
        } else {
            var total = items.reduce(function (sum, i) { return sum + i.count; }, 0);
            body = items.map(function (i) {
                var pct = total ? Math.round((i.count / total) * 100) : 0;
                return '<div class="ax-row"><div class="ax-row-top"><span>' + esc(i.label) +
                    '</span><span>' + i.count + ' &middot; ' + pct + '%</span></div>' +
                    '<div class="ax-track"><div class="ax-fill" style="width:' + pct + '%"></div></div></div>';
            }).join("");
        }

        return '<div class="panel"><div class="panel-header"><h3>' + esc(title) + '</h3></div>' + body + '</div>';
    }

    function recentTable(rows) {
        if (!rows || !rows.length) {
            return '<div class="ax-empty">Your videos will be listed here.</div>';
        }

        var body = rows.map(function (r) {
            var status = String(r.status || "").toLowerCase();
            return '<tr><td class="ax-topic">' + esc(r.topic || "Untitled") + '</td>' +
                '<td><span class="status ' + esc(status) + '">' + esc(status) + '</span></td>' +
                '<td>' + esc(r.duration) + 's</td>' +
                '<td>' + (status === "failed" ? "0" : esc(r.credits)) + '</td>' +
                '<td>' + esc(whenText(r.created_at)) + '</td></tr>';
        }).join("");

        return '<div class="ax-scroll"><table class="ax-table"><thead><tr><th>Topic</th><th>Status</th>' +
            '<th>Length</th><th>Credits</th><th>Created</th></tr></thead><tbody>' + body + '</tbody></table></div>';
    }

    function render(data) {
        var box = ensureBox();
        if (!box) { return; }

        var c = data.credits || {};
        var o = data.outcomes || {};
        var b = data.breakdown || {};

        // Credits card notes.
        var runwayValue;
        var runwayNote;

        if (c.refills_daily) {
            runwayValue = "Daily";
            runwayNote = "Free credits refill every day (UTC).";
        } else if (c.runway_days == null) {
            runwayValue = "—";
            runwayNote = "Make a few videos to see how long your credits last.";
        } else {
            runwayValue = c.runway_days + (c.runway_days === 1 ? " day" : " days");
            runwayNote = "At your last 7 days' pace" +
                (c.plan_days_left != null ? ". Plan ends in " + c.plan_days_left + (c.plan_days_left === 1 ? " day." : " days.") : ".");
        }

        var rate = o.success_rate == null ? "—" : o.success_rate + "%";
        var rateNote = (o.completed || 0) + " completed, " + (o.failed || 0) + " failed" +
            (o.in_progress ? ", " + o.in_progress + " in progress" : "");

        var html =
            '<div class="ax-grid">' +
                card("Credits Available", c.balance, "") +
                card("Credits Used (" + data.days + " days)", c.spent_30d, "Failed videos are refunded and not counted.") +
                card("Credits Last About", runwayValue, runwayNote, "font-size:26px") +
            '</div>' +
            '<div class="ax-grid">' +
                card("Videos (" + data.days + " days)", data.total, "") +
                card("Success Rate", rate, rateNote) +
                card("Failed Videos", o.failed || 0, "Credits are returned automatically.") +
            '</div>' +
            '<div class="panel"><div class="panel-header"><h3>Videos per day</h3><span>Last ' + data.days + ' days (UTC)</span></div>' +
                (data.total ? chart(data.series) : '<div class="ax-empty">No videos in this period yet. Your daily activity will appear here.</div>') +
            '</div>' +
            '<div class="ax-two">' +
                breakdown("By template", b.template) +
                breakdown("By length", b.length) +
            '</div>' +
            '<div class="ax-two">' +
                breakdown("By format", b.format) +
                breakdown("By voice", b.voice) +
            '</div>' +
            '<div class="panel"><div class="panel-header"><h3>Recent videos</h3><span>Latest 10</span></div>' +
                recentTable(data.recent) +
            '</div>';

        box.innerHTML = html;

        // Solo / Dialogue now cover every video, not just the latest 20.
        var n = data.narration_counts || {};
        var solo = Number(n.solo) || 0;
        var dialogue = Number(n.dialogue) || 0;
        var set = function (id, text) {
            var node = document.getElementById(id);
            if (node) { node.textContent = text; }
        };
        set("analyticsSolo", solo);
        set("analyticsDialogue", dialogue);
        set("analyticsTopMode", solo + dialogue === 0 ? "—" : solo === dialogue ? "Tied" : dialogue > solo ? "Dialogue" : "Solo");
    }

    function showError(message) {
        var box = ensureBox();
        if (box) {
            box.innerHTML = '<div class="panel"><div class="ax-empty">' + esc(message) + '</div></div>';
        }
    }

    var original = window.loadAnalytics;

    window.loadAnalytics = async function () {
        if (typeof original === "function") {
            try { await original.apply(this, arguments); } catch (e) { /* keep going */ }
        }

        try {
            var data = await window.api("/analytics");
            render(data);
        } catch (error) {
            showError((error && error.message) || "Could not load analytics.");
        }
    };
})();

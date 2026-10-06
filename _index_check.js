

    /* =====================================================
       APPLICATION STATE
    ===================================================== */

    let currentUser = null;
    let currentPage = "dashboard";
    let statusTimer = null;


    /* =====================================================
       BASIC HELPERS
    ===================================================== */

    function showToast(message) {

        const toast = document.getElementById("toast");

        toast.textContent = message;
        toast.classList.add("show");

        setTimeout(() => {
            toast.classList.remove("show");
        }, 3000);
    }


    function showAuthMessage(message, type = "error") {

        const box = document.getElementById("authMessage");

        box.textContent = message;
        box.className = "auth-message " + type;

    }


    function clearAuthMessage() {

        const box = document.getElementById("authMessage");

        box.textContent = "";
        box.className = "auth-message";

    }


    async function api(url, options = {}) {

        const response = await fetch(url, {
            credentials: "include",
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {})
            }
        });

        let data = null;

        try {
            data = await response.json();
        } catch {
            data = null;
        }

        if (!response.ok) {

            let message = "Something went wrong.";

            if (data) {

                if (typeof data.detail === "string") {
                    message = data.detail;
                }

                else if (
                    data.detail &&
                    typeof data.detail.message === "string"
                ) {
                    message = data.detail.message;
                }
            }

            throw new Error(message);
        }

        return data;
    }


    /* =====================================================
       AUTH TABS
    ===================================================== */

    function showLogin() {

        document
            .getElementById("loginTab")
            .classList.add("active");

        document
            .getElementById("signupTab")
            .classList.remove("active");

        document
            .getElementById("loginForm")
            .classList.remove("hidden");

        document
            .getElementById("signupForm")
            .classList.add("hidden");

        document.getElementById("authTitle").textContent =
            "Welcome back";

        document.getElementById("authSubtitle").textContent =
            "Sign in to continue to ClipPirate.";

        clearAuthMessage();
    }


    function showSignup() {

        document
            .getElementById("loginTab")
            .classList.remove("active");

        document
            .getElementById("signupTab")
            .classList.add("active");

        document
            .getElementById("loginForm")
            .classList.add("hidden");

        document
            .getElementById("signupForm")
            .classList.remove("hidden");

        document.getElementById("authTitle").textContent =
            "Create your account";

        document.getElementById("authSubtitle").textContent =
            "Start creating videos with ClipPirate.";

        clearAuthMessage();
    }


    /* =====================================================
       AUTH CHECK
    ===================================================== */

    async function checkAuthentication() {

        try {

            const data = await api("/auth/me");

            if (data && data.user) {

                currentUser = data.user;

                enterApplication();

                return true;
            }

        } catch (error) {
            // User is not logged in.
        }

        showAuthScreen();

        return false;
    }


    function showAuthScreen() {

        document
            .getElementById("authScreen")
            .classList.remove("hidden");

        document
            .getElementById("appScreen")
            .classList.add("hidden");

    }


    function enterApplication() {

        document
            .getElementById("authScreen")
            .classList.add("hidden");

        document
            .getElementById("appScreen")
            .classList.remove("hidden");

        updateUserInterface();

        loadDashboard();

        navigate("dashboard");
    }


    function updateUserInterface() {

        if (!currentUser) {
            return;
        }

        const name =
            currentUser.name ||
            "Creator";

        const email =
            currentUser.email ||
            "";

        document.getElementById("welcomeName").textContent =
            name;

        document.getElementById("sidebarUserName").textContent =
            name;

        document.getElementById("sidebarUserEmail").textContent =
            email;

        document.getElementById("settingsName").textContent =
            name;

        document.getElementById("settingsEmail").textContent =
            email;

        const credits =
            currentUser.credits ??
            currentUser.credit_balance ??
            "—";

        document.getElementById("topCredits").textContent =
            credits;

        document.getElementById("dashboardCredits").textContent =
            credits;

        document.getElementById("accountCredits").textContent =
            credits;

        const plan =
            currentUser.plan ||
            currentUser.plan_name ||
            "Free";

        document.getElementById("dashboardPlan").textContent =
            plan;

        document.getElementById("accountPlan").textContent =
            plan;
    }


    /* =====================================================
       LOGIN
    ===================================================== */

    document
        .getElementById("loginForm")
        .addEventListener("submit", async function(event) {

            event.preventDefault();

            clearAuthMessage();

            const button =
                document.getElementById("loginButton");

            button.disabled = true;
            button.textContent = "Logging in...";

            try {

                const email =
                    document
                        .getElementById("loginEmail")
                        .value
                        .trim();

                const password =
                    document
                        .getElementById("loginPassword")
                        .value;

                const data = await api(
                    "/auth/login",
                    {
                        method: "POST",
                        body: JSON.stringify({
                            email,
                            password
                        })
                    }
                );

                currentUser = data.user;

                enterApplication();

            } catch (error) {

                showAuthMessage(
                    error.message || "Login failed."
                );

            } finally {

                button.disabled = false;
                button.textContent = "Login";

            }

        });


    /* =====================================================
       SIGNUP
    ===================================================== */

    document
        .getElementById("signupForm")
        .addEventListener("submit", async function(event) {

            event.preventDefault();

            clearAuthMessage();

            const button =
                document.getElementById("signupButton");

            button.disabled = true;
            button.textContent = "Creating account...";

            try {

                const name =
                    document
                        .getElementById("signupName")
                        .value
                        .trim();

                const email =
                    document
                        .getElementById("signupEmail")
                        .value
                        .trim();

                const password =
                    document
                        .getElementById("signupPassword")
                        .value;

                const data = await api(
                    "/auth/signup",
                    {
                        method: "POST",
                        body: JSON.stringify({
                            name,
                            email,
                            password
                        })
                    }
                );

                currentUser = data.user;

                enterApplication();

            } catch (error) {

                showAuthMessage(
                    error.message ||
                    "Could not create the account."
                );

            } finally {

                button.disabled = false;
                button.textContent = "Create Account";

            }

        });


    /* =====================================================
       LOGOUT
    ===================================================== */

    async function logout() {

        try {
            await api(
                "/auth/logout",
                {
                    method: "POST"
                }
            );
        } catch (error) {
            console.error(error);
        }

        currentUser = null;

        if (statusTimer) {
            clearTimeout(statusTimer);
            statusTimer = null;
        }

        showAuthScreen();

        showLogin();

        document.getElementById("loginPassword").value = "";

        showToast("You have been logged out.");
    }


    /* =====================================================
       NAVIGATION
    ===================================================== */

    function navigate(page) {

        currentPage = page;

        document
            .querySelectorAll(".page")
            .forEach(section => {
                section.classList.add("hidden");
            });

        const target =
            document.getElementById(
                "page-" + page
            );

        if (target) {
            target.classList.remove("hidden");
        }

        document
            .querySelectorAll(".nav-button")
            .forEach(button => {

                button.classList.toggle(
                    "active",
                    button.dataset.page === page
                );

            });


        const titles = {

            dashboard: "Dashboard",

            projects: "Projects",

            create: "Create Video",

            templates: "Templates",

            analytics: "Analytics",

            settings: "Settings"

        };

        document.getElementById("pageTitle").textContent =
            titles[page] || "ClipPirate";


        if (page === "dashboard") {
            loadDashboard();
        }

        if (page === "projects") {
            loadProjects();
        }

        if (page === "analytics") {
            loadAnalytics();
        }

        if (page === "settings") {
            loadPreferences();
        }

        if (page === "create") {
            loadPreferencesIntoCreateForm();
        }

    }


    /* =====================================================
       DASHBOARD
    ===================================================== */

    async function loadDashboard() {

        try {

            const [
                creditsData,
                projectsData,
                usageData
            ] = await Promise.all([

                api("/credits"),

                api("/projects"),

                api("/usage")

            ]);


            if (creditsData.user) {

                currentUser =
                    creditsData.user;

                updateUserInterface();

            }


            const projects =
                projectsData.projects || [];

            document.getElementById(
                "dashboardProjects"
            ).textContent =
                projects.length;


            document.getElementById(
                "dashboardUsage"
            ).textContent =
                usageData.platform_used_today ?? 0;


            renderRecentProjects(projects);


        } catch (error) {

            console.error(
                "Dashboard error:",
                error
            );

        }

    }


    function renderRecentProjects(projects) {

        const container =
            document.getElementById(
                "recentProjects"
            );

        if (!projects.length) {

            container.innerHTML = `
                <div class="empty-state" style="padding:30px 10px">
                    No projects yet.
                </div>
            `;

            return;
        }


        const recent =
            projects.slice(0, 4);


        container.innerHTML =
            recent.map(project => {

                const status =
                    String(
                        project.status || "pending"
                    ).toLowerCase();

                return `
                    <div
                        style="
                            padding:12px 0;
                            border-bottom:1px solid var(--border);
                        "
                    >

                        <div
                            style="
                                display:flex;
                                justify-content:space-between;
                                gap:10px;
                            "
                        >

                            <strong
                                style="
                                    font-size:13px;
                                    overflow:hidden;
                                    text-overflow:ellipsis;
                                    white-space:nowrap;
                                "
                            >
                                ${escapeHtml(
                                    project.topic ||
                                    "Untitled Project"
                                )}
                            </strong>

                            <span class="status ${status}">
                                ${escapeHtml(status)}
                            </span>

                        </div>

                    </div>
                `;

            })
            .join("");

    }


    /* =====================================================
       PROJECTS
    ===================================================== */

    async function loadProjects() {

        const container =
            document.getElementById(
                "projectList"
            );

        container.innerHTML =
            "Loading projects...";


        try {

            const data =
                await api("/projects");

            const projects =
                data.projects || [];


            if (!projects.length) {

                container.innerHTML = `
                    <div class="empty-state">

                        <h3 style="color:white;margin-bottom:8px">
                            No projects yet
                        </h3>

                        <p style="margin-bottom:18px">
                            Create your first video to see it here.
                        </p>

                        <button
                            class="create-btn"
                            onclick="navigate('create')"
                        >
                            Create Video
                        </button>

                    </div>
                `;

                return;
            }


            container.innerHTML =
                projects.map(project => {

                    const status =
                        String(
                            project.status || "pending"
                        ).toLowerCase();

                    const videoUrl =
                        project.video_url ||
                        project.download_url ||
                        "";


                    return `
                        <div class="project-card">

                            <div class="project-info">

                                <h3>
                                    ${escapeHtml(
                                        project.topic ||
                                        "Untitled Project"
                                    )}
                                </h3>

                                <div class="project-meta">

                                    Duration:
                                    ${escapeHtml(
                                        String(
                                            project.duration ??
                                            "-"
                                        )
                                    )} sec

                                    &nbsp;•&nbsp;

                                    Ratio:
                                    ${escapeHtml(
                                        project.aspect_ratio ||
                                        "-"
                                    )}

                                </div>

                            </div>


                            <div class="project-actions">

                                <span class="status ${status}">
                                    ${escapeHtml(status)}
                                </span>

                                ${
                                    videoUrl
                                    ?
                                    `
                                    <a
                                        href="${escapeAttribute(videoUrl)}"
                                        target="_blank"
                                        class="secondary-btn"
                                        style="text-decoration:none"
                                    >
                                        Open
                                    </a>
                                    `
                                    :
                                    ""
                                }

                            </div>

                        </div>
                    `;

                })
                .join("");


        } catch (error) {

            container.innerHTML = `
                <div class="empty-state">
                    ${escapeHtml(error.message)}
                </div>
            `;

        }

    }


    /* =====================================================
       CREATE VIDEO
    ===================================================== */

    document
        .getElementById("generateForm")
        .addEventListener("submit", async function(event) {

            event.preventDefault();

            const topic =
                document
                    .getElementById("videoTopic")
                    .value
                    .trim();

            const duration =
                Number(
                    document
                        .getElementById("videoDuration")
                        .value
                );

            const aspectRatio =
                document
                    .getElementById("videoRatio")
                    .value;

            const voice =
                document
                    .getElementById("videoVoice")
                    .value;

            const captions =
                document
                    .getElementById("videoCaptions")
                    .checked;


            if (!topic) {

                showGenerationStatus(
                    "Please enter a video topic.",
                    "error"
                );

                return;
            }


            const button =
                document.getElementById(
                    "generateButton"
                );

            button.disabled = true;

            button.textContent =
                "Starting generation...";


            showGenerationStatus(
                "Creating your video job...",
                "loading"
            );


            try {

                const data =
                    await api(
                        "/generate",
                        {
                            method: "POST",

                            body: JSON.stringify({
                                topic,
                                duration,
                                aspect_ratio:
                                    aspectRatio,
                                voice,
                                captions
                            })
                        }
                    );


                if (
                    data.credits &&
                    data.credits.credits !== undefined
                ) {

                    currentUser =
                        data.credits;

                    updateUserInterface();

                }


                showGenerationStatus(
                    "Video generation started. Processing...",
                    "loading"
                );


                pollJobStatus(
                    data.job_id
                );


            } catch (error) {

                showGenerationStatus(
                    error.message ||
                    "Generation failed.",
                    "error"
                );

                button.disabled = false;

                button.textContent =
                    "Generate Video";

            }

        });


    function showGenerationStatus(
        message,
        type
    ) {

        const box =
            document.getElementById(
                "generationStatus"
            );

        box.textContent = message;

        box.className =
            "generation-status show " +
            type;

    }


    /* =====================================================
       JOB STATUS
    ===================================================== */

    async function pollJobStatus(jobId) {

        try {

            const data =
                await api(
                    "/status/" +
                    encodeURIComponent(jobId)
                );


            if (data.status === "completed") {

                showGenerationStatus(
                    "Video generated successfully!",
                    "success"
                );

                const button =
                    document.getElementById(
                        "generateButton"
                    );

                button.disabled = false;

                button.textContent =
                    "Generate Video";


                if (data.download_url) {

                    const box =
                        document.getElementById(
                            "generationStatus"
                        );

                    box.innerHTML = `
                        Video generated successfully.

                        <br><br>

                        <a
                            href="${escapeAttribute(
                                data.download_url
                            )}"
                            target="_blank"
                            class="secondary-btn"
                            style="
                                display:inline-block;
                                text-decoration:none;
                            "
                        >
                            Open Video
                        </a>
                    `;

                }


                loadDashboard();

                return;
            }


            if (data.status === "failed") {

                showGenerationStatus(
                    data.error ||
                    "Video generation failed.",
                    "error"
                );

                const button =
                    document.getElementById(
                        "generateButton"
                    );

                button.disabled = false;

                button.textContent =
                    "Generate Video";

                return;
            }


            showGenerationStatus(
                "Video is being generated...",
                "loading"
            );


            statusTimer =
                setTimeout(
                    () => pollJobStatus(jobId),
                    3000
                );


        } catch (error) {

            showGenerationStatus(
                error.message ||
                "Could not check generation status.",
                "error"
            );

            const button =
                document.getElementById(
                    "generateButton"
                );

            button.disabled = false;

            button.textContent =
                "Generate Video";

        }

    }


    /* =====================================================
       TEMPLATES
    ===================================================== */

    function useTemplate(templateName) {

        localStorage.setItem(
            "clipPirateTemplate",
            templateName
        );

        navigate("create");

        showToast(
            templateName +
            " template selected."
        );

    }


    /* =====================================================
       ANALYTICS
    ===================================================== */

    async function loadAnalytics() {

        try {

            const data =
                await api("/usage");

            const used =
                Number(
                    data.platform_used_today || 0
                );

            const limit =
                Number(
                    data.platform_daily_limit || 0
                );

            const remaining =
                Math.max(
                    limit - used,
                    0
                );


            document.getElementById(
                "analyticsToday"
            ).textContent = used;


            document.getElementById(
                "analyticsLimit"
            ).textContent = limit;


            document.getElementById(
                "analyticsRemaining"
            ).textContent = remaining;


            const percentage =
                limit > 0
                ? Math.min(
                    (used / limit) * 100,
                    100
                )
                : 0;


            document.getElementById(
                "usageFill"
            ).style.width =
                percentage + "%";


            document.getElementById(
                "usageText"
            ).textContent =
                `${used} of ${limit} generations used today.`;

        } catch (error) {

            document.getElementById(
                "usageText"
            ).textContent =
                error.message;

        }

    }


    /* =====================================================
       SETTINGS
    ===================================================== */
/* =====================================================
   PLANS (Settings -> Account)
===================================================== */

const PLAN_PRICES_KSH = { free: 0, starter: 300, pro: 600, mega: 1200 };
let currentPlanId = "free";

function planApiBase() {
    return typeof API_BASE !== "undefined" ? API_BASE : "";
}

function formatPlanPrice(plan) {
    const id = String(plan.id).toLowerCase();
    const price = id in PLAN_PRICES_KSH ? PLAN_PRICES_KSH[id] : Number(plan.price) || 0;
    return price === 0 ? "KSh 0" : `KSh ${price.toLocaleString()}/month`;
}

function renderPlans(plans) {
    const grid = document.getElementById("plansGrid");
    if (!grid) return;

    grid.innerHTML = plans.map(plan => {
        const isCurrent =
            String(plan.id).toLowerCase() === currentPlanId ||
            String(plan.name).toLowerCase() === currentPlanId;

        return `
            <div style="position:relative;padding:20px;border-radius:14px;
                        background:rgba(255,255,255,0.04);
                        border:1px solid ${isCurrent ? "#8c72ff" : "rgba(255,255,255,0.1)"};">
                ${isCurrent ? `<div style="position:absolute;top:12px;right:12px;font-size:11px;font-weight:700;color:#8c72ff;">CURRENT</div>` : ""}
                <div style="font-size:15px;font-weight:700;">${plan.name}</div>
                <div style="font-size:22px;font-weight:800;margin:8px 0 2px;">${formatPlanPrice(plan)}</div>
                <div style="font-size:13px;opacity:.7;">${plan.credits} credits</div>
                <button
                    type="button"
                    ${isCurrent ? "disabled" : ""}
                    onclick="upgradePlan('${plan.id}')"
                    style="margin-top:16px;width:100%;padding:10px;border:0;border-radius:10px;
                           font-weight:700;cursor:${isCurrent ? "default" : "pointer"};
                           background:${isCurrent ? "rgba(255,255,255,0.1)" : "#8c72ff"};color:#fff;">
                    ${isCurrent ? "Current Plan" : `Upgrade to ${plan.name}`}
                </button>
            </div>
        `;
    }).join("");
}

async function loadPlans() {
    if (!document.getElementById("plansGrid")) return;

    try {
        if (typeof currentUser !== "undefined" && currentUser) {
            currentPlanId = String(
                currentUser.plan || currentUser.plan_name || "free"
            ).toLowerCase();
        }

        const response = await fetch(`${planApiBase()}/plans`, { credentials: "include" });
        const data = await response.json();
        renderPlans(data.plans || data);
    } catch (error) {
        console.error(error);
    }
}

async function upgradePlan(planId) {
    try {
        const response = await fetch(
            `${planApiBase()}/subscription/dev-upgrade/${encodeURIComponent(planId)}`,
            { method: "POST", credentials: "include" }
        );
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `Upgrade failed (${response.status})`);
        }

        const user = data.user;
        currentPlanId = String(planId).toLowerCase();

        if (typeof currentUser !== "undefined" && currentUser) {
            currentUser.plan = user.plan_name;
            currentUser.plan_name = user.plan_name;
            currentUser.credits = user.credits;
        }

        ["dashboardPlan", "accountPlan"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = user.plan_name;
        });
        ["topCredits", "dashboardCredits", "accountCredits"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = user.credits;
        });

        alert(`Plan changed to ${user.plan_name}. Credits: ${user.credits}`);
        loadPlans();

    } catch (error) {
        console.error(error);
        alert(error.message || "Unable to change plan.");
    }
}
    function showSettingsPanel(
        panel,
        button
    ) {

        document
            .querySelectorAll(".settings-panel")
            .forEach(item => {
                item.classList.remove("active");
            });


        document
            .querySelectorAll(".settings-nav button")
            .forEach(item => {
                item.classList.remove("active");
            });


        document
            .getElementById(
                "settings-" + panel
            )
            .classList.add("active");


        button.classList.add("active");

    }


    function loadPreferences() {

        const saved =
            getPreferences();


        document.getElementById(
            "prefDuration"
        ).value =
            saved.duration;


        document.getElementById(
            "prefRatio"
        ).value =
            saved.ratio;


        document.getElementById(
            "prefVoice"
        ).value =
            saved.voice;


        document.getElementById(
            "prefCaptions"
        ).checked =
            saved.captions;

    }


    function getPreferences() {

        const defaults = {

            duration: "30",

            ratio: "9:16",

            voice: "professional",

            captions: true

        };


        try {

            const saved =
                JSON.parse(
                    localStorage.getItem(
                        "clipPiratePreferences"
                    )
                );

            return {
                ...defaults,
                ...(saved || {})
            };

        } catch {

            return defaults;

        }

    }


    function savePreferences() {

        const preferences = {

            duration:
                document.getElementById(
                    "prefDuration"
                ).value,

            ratio:
                document.getElementById(
                    "prefRatio"
                ).value,

            voice:
                document.getElementById(
                    "prefVoice"
                ).value,

            captions:
                document.getElementById(
                    "prefCaptions"
                ).checked

        };


        localStorage.setItem(
            "clipPiratePreferences",
            JSON.stringify(preferences)
        );


        showToast(
            "Preferences saved."
        );

    }


    function loadPreferencesIntoCreateForm() {

        const preferences =
            getPreferences();


        const template =
            localStorage.getItem(
                "clipPirateTemplate"
            );


        document.getElementById(
            "videoDuration"
        ).value =
            preferences.duration;


        document.getElementById(
            "videoRatio"
        ).value =
            preferences.ratio;


        document.getElementById(
            "videoVoice"
        ).value =
            preferences.voice;


        document.getElementById(
            "videoCaptions"
        ).checked =
            preferences.captions;


        if (template) {

            const topic =
                document.getElementById(
                    "videoTopic"
                );


            if (!topic.value.trim()) {

                const templatePrompts = {

                    "Viral Facts":
                        "Amazing facts about ",

                    "Amazing Places":
                        "Amazing places in ",

                    "Did You Know":
                        "Did you know facts about ",

                    "Storytelling":
                        "A fascinating short story about ",

                    "Motivation":
                        "An inspiring story about ",

                    "Top 5 List":
                        "Top 5 facts about "

                };


                topic.placeholder =
                    templatePrompts[template] ||
                    "Enter your video topic...";

            }


            localStorage.removeItem(
                "clipPirateTemplate"
            );

        }

    }


    /* =====================================================
       SECURITY / HTML HELPERS
    ===================================================== */

    function escapeHtml(value) {

        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");

    }


    function escapeAttribute(value) {

        return escapeHtml(value);

    }


    /* =====================================================
       START APPLICATION
    ===================================================== */

    document.addEventListener(
        "DOMContentLoaded",
        async function() {

            loadPreferences();

            await checkAuthentication();

        }
    );


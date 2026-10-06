

const API_BASE = window.location.origin;


/* ==========================================
   API HELPER
========================================== */

async function api(url) {

    const response = await fetch(
        `${API_BASE}${url}`,
        {
            credentials: "include"
        }
    );

    if (!response.ok) {

        let message = `Request failed (${response.status})`;

        try {

            const data = await response.json();

            message =
                data.detail ||
                data.error ||
                message;

        } catch (_) {}

        throw new Error(message);
    }

    return response.json();
}


/* ==========================================
   LOAD DASHBOARD
========================================== */

function renderPlans(plans, currentPlan) {

    const grid = document.getElementById("plansGrid");

    if (!grid) return;

    grid.innerHTML = plans.map(plan => {

        const active = plan.id === currentPlan;

        return `
            <div class="stat-card" style="position:relative;">
                ${active ? `<div style="position:absolute;top:12px;right:12px;font-size:11px;color:#8c72ff;font-weight:700;">CURRENT</div>` : ""}

                <div class="stat-title">
                    ${plan.name}
                </div>

                <div class="stat-value">
                    ${plan.credits}
                </div>

                <div class="stat-description">
                    Credits · ${plan.price === 0 ? "Free" : "Monthly"}
                </div>

                <button
                    type="button"
                    class="project-button"
                    style="margin-top:16px;width:100%;"
                    ${active ? "disabled" : ""}
                    onclick="upgradePlan('${plan.id}')"
                >
                    ${active ? "Current Plan" : `Upgrade to ${plan.name}`}
                </button>
            </div>
        `;

    }).join("");
}


async function upgradePlan(plan) {

    try {

        const response = await fetch(
            `${API_BASE}/subscription/dev-upgrade/${encodeURIComponent(plan)}`,
            {
                method: "POST",
                credentials: "include"
            }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.detail || `Upgrade failed (${response.status})`
            );
        }

        alert(
            `Plan changed to ${data.user.plan_name}. Credits: ${data.user.credits}`
        );

        await loadDashboard();

    } catch (error) {

        console.error(error);
        alert(error.message || "Unable to change plan.");
    }
}





async function loadDashboard() {

    try {

        const [
            creditsData,
            usageData,
            projectsData,
            plansData
        ] = await Promise.all([

            api("/credits"),

            api("/usage"),

            api("/projects"),

            api("/plans")

        ]);


        /* ----------------------------------
           USER
        ---------------------------------- */

        const user =
            creditsData.user ||
            usageData.user;

        if (user) {

            const name =
                user.name ||
                user.email ||
                "User";

            document.getElementById(
                "userName"
            ).textContent = name;

            document.getElementById(
                "avatar"
            ).textContent =
                name.charAt(0).toUpperCase();

            const plan =
                user.plan ||
                "free";

            document.getElementById(
                "userPlan"
            ).textContent =
                `${plan.charAt(0).toUpperCase() + plan.slice(1)} plan`;

            document.getElementById(
                "plan"
            ).textContent =
                plan.charAt(0).toUpperCase() +
                plan.slice(1);

        }


        /* ----------------------------------
           PLANS
        ---------------------------------- */

        renderPlans(
            plansData.plans || [],
            user?.plan || "free"
        );


        /* ----------------------------------
           CREDITS
        ---------------------------------- */

        const creditUser =
            creditsData.user;

        const credits =
            creditUser?.credits ??
            creditUser?.remaining_credits ??
            0;

        document.getElementById(
            "credits"
        ).textContent = credits;


        /* ----------------------------------
           USAGE
        ---------------------------------- */

        const used =
            usageData.platform_used_today ??
            0;

        const limit =
            usageData.platform_daily_limit ??
            0;

        document.getElementById(
            "todayUsage"
        ).textContent =
            `${used}`;

        document.getElementById(
            "usedCredits"
        ).textContent =
            `${used} used`;

        document.getElementById(
            "totalCredits"
        ).textContent =
            `${limit} daily limit`;

        let percentage = 0;

        if (limit > 0) {

            percentage =
                Math.min(
                    100,
                    (used / limit) * 100
                );

        }

        document.getElementById(
            "usageFill"
        ).style.width =
            `${percentage}%`;

        document.getElementById(
            "creditUsageText"
        ).textContent =
            `${used} of ${limit} daily generations used`;


        /* ----------------------------------
           PLANS
        ---------------------------------- */

        /* ----------------------------------
           PROJECTS
        ---------------------------------- */

        const projects =
            projectsData.projects || [];

        document.getElementById(
            "projectCount"
        ).textContent =
            projects.length;


        renderProjects(projects);


    } catch (error) {

        console.error(
            "Dashboard error:",
            error
        );

        document.getElementById(
            "projects"
        ).innerHTML = `
            <div class="empty">
                Unable to load dashboard data.
                <br><br>
                ${escapeHtml(error.message)}
            </div>
        `;

    }

}


/* ==========================================
   RENDER PROJECTS
========================================== */

function renderProjects(projects) {

    const container =
        document.getElementById(
            "projects"
        );

    if (!projects.length) {

        container.innerHTML = `
            <div class="empty">
                You haven't created any videos yet.
                <br><br>
                Create your first video to see it here.
            </div>
        `;

        return;
    }

    const recent =
        projects
            .slice()
            .reverse()
            .slice(0, 6);

    container.innerHTML =
        recent.map(project => {

            const title =
                project.topic ||
                project.name ||
                "Untitled project";

            const status =
                project.status ||
                "pending";

            const duration =
                project.duration
                ? `${project.duration}s`
                : "";

            const videoUrl =
                project.video_url ||
                project.download_url ||
                "";

            let videoFilename = "";

            if (videoUrl) {

                try {

                    videoFilename =
                        decodeURIComponent(
                            videoUrl
                                .split("/")
                                .pop()
                        );

                } catch (_) {

                    videoFilename =
                        videoUrl
                            .split("/")
                            .pop();
                }
            }

            const isCompleted =
                status.toLowerCase() === "completed" &&
                videoFilename;

            return `

                <div class="project">

                    <div class="project-info">

                        <div class="project-title">
                            ${escapeHtml(title)}
                        </div>

                        <div class="project-meta">
                            ${escapeHtml(status)}
                            ${duration ? " • " + duration : ""}
                        </div>

                    </div>


                    ${
                        isCompleted
                        ?
                        `
                        <div class="project-actions">

                            <a
                                class="project-button"
                                href="${escapeHtml(videoUrl)}"
                                target="_blank"
                                rel="noopener"
                            >
                                Open Video
                            </a>

                            <button
                                class="project-button download"
                                type="button"
                                onclick="downloadProjectVideo(
                                    '${escapeHtml(videoFilename)}',
                                    this
                                )"
                            >
                                Download
                            </button>

                        </div>
                        `
                        :
                        `
                        <div class="status ${escapeHtml(status)}">
                            ${escapeHtml(status)}
                        </div>
                        `
                    }

                </div>
            `;

        }).join("");
}

/* ==========================================
   DOWNLOAD VIDEO
========================================== */

async function downloadProjectVideo(
    filename,
    button
) {

    if (!filename) {

        alert(
            "The video file could not be identified."
        );

        return;
    }

    const originalText =
        button.textContent;

    try {

        button.disabled = true;
        button.textContent = "Downloading...";

        const response = await fetch(
            `${API_BASE}/download/${encodeURIComponent(filename)}`,
            {
                method: "GET",
                credentials: "include"
            }
        );

        if (!response.ok) {

            let message =
                `Download failed (${response.status})`;

            try {

                const data =
                    await response.json();

                message =
                    data.detail ||
                    data.error ||
                    message;

            } catch (_) {}

            throw new Error(message);
        }

        const blob =
            await response.blob();

        const blobUrl =
            URL.createObjectURL(blob);

        const link =
            document.createElement("a");

        link.href =
            blobUrl;

        link.download =
            filename;

        document.body.appendChild(
            link
        );

        link.click();

        link.remove();

        /*
         * Give the browser time to begin
         * the download before releasing
         * the object URL.
         */

        setTimeout(
            () => URL.revokeObjectURL(blobUrl),
            1000
        );

        button.textContent =
            "Downloaded";

        setTimeout(
            () => {
                button.disabled = false;
                button.textContent = originalText;
            },
            1500
        );

    } catch (error) {

        console.error(
            "Video download failed:",
            error
        );

        button.disabled = false;
        button.textContent = originalText;

        alert(
            `Unable to download the video.\n\n${error.message}`
        );
    }
}

/* ==========================================
   ESCAPE HTML
========================================== */

function escapeHtml(value) {

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");

}



/* ==========================================
   VIDEO GENERATION WORKFLOW
========================================== */

let activeJobId = null;
let pollTimer = null;
let generationInProgress = false;

const GENERATION_POLL_MS = 2500;

function getSelectedDuration() {
    const selected = document.querySelector(
        'input[name="duration"]:checked'
    );
    return Number(selected?.value || 30);
}

function updateGenerateHint() {
    const duration = getSelectedDuration();

    let cost = 2;

    if (duration > 30 && duration <= 45) {
        cost = 4;
    } else if (duration > 45) {
        cost = 6;
    }

    const hint = document.getElementById("generateHint");

    if (hint) {
        hint.textContent =
            `${duration}-second videos use ${cost} credit${cost === 1 ? "" : "s"}.`;
    }
}

function scrollToGenerator(event) {
    if (event) event.preventDefault();

    const generator = document.getElementById("generator");

    if (generator) {
        generator.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

        setTimeout(() => {
            document.getElementById("topic")?.focus();
        }, 350);
    }
}

function setGenerationStatus(
    mode,
    title,
    message,
    error = ""
) {
    const box = document.getElementById("generationStatus");
    const spinner = document.getElementById("statusSpinner");
    const statusTitle = document.getElementById("statusTitle");
    const statusMessage = document.getElementById("statusMessage");
    const errorBox = document.getElementById("generationError");
    const progress = document.getElementById("progressTrack");

    box.className = `generation-status show ${mode || ""}`;

    statusTitle.textContent = title;
    statusMessage.textContent = message;
    errorBox.textContent = error || "";

    spinner.style.display =
        mode === "success" || mode === "error"
            ? "none"
            : "block";

    progress.style.display =
        mode === "success" || mode === "error"
            ? "none"
            : "block";
}

function resetVideoResult() {
    const result = document.getElementById("videoResult");
    const video = document.getElementById("resultVideo");
    const download = document.getElementById("downloadVideo");
    const scriptPreview = document.getElementById("scriptPreview");

    result.classList.remove("show");
    scriptPreview.classList.remove("show");

    video.pause();
    video.removeAttribute("src");
    video.load();

    download.href = "#";
}

function setGeneratingState(active) {
    generationInProgress = active;

    const button = document.getElementById("generateButton");
    const inputs = document.querySelectorAll(
        "#generatorForm input, #generatorForm select"
    );

    button.disabled = active;
    button.textContent = active
        ? "Generating..."
        : "Generate Video";

    inputs.forEach(input => {
        input.disabled = active;
    });
}

function showCompletedVideo(data) {
    const videoUrl = data.download_url;

    if (!videoUrl) {
        throw new Error(
            "The server completed the job but did not return a video URL."
        );
    }

    const video = document.getElementById("resultVideo");
    const download = document.getElementById("downloadVideo");
    const result = document.getElementById("videoResult");

    const absoluteUrl =
        videoUrl.startsWith("http")
            ? videoUrl
            : `${API_BASE}${videoUrl}`;

    video.src = absoluteUrl;
    download.href = absoluteUrl;

    result.classList.add("show");

    if (data.script) {
        document.getElementById(
            "scriptPreviewText"
        ).textContent = data.script;

        document.getElementById(
            "scriptPreview"
        ).classList.add("show");
    }

    setGenerationStatus(
        "success",
        "Video ready",
        "Your video has finished generating. You can preview or download it."
    );

    setGeneratingState(false);

    loadDashboard();
}

async function pollJobStatus(jobId) {
    if (!generationInProgress) {
        return;
    }

    try {
        const response = await fetch(
            `${API_BASE}/status/${encodeURIComponent(jobId)}`,
            {
                credentials: "include"
            }
        );

        let data = null;

        try {
            data = await response.json();
        } catch (_) {
            throw new Error(
                `Invalid server response (${response.status}).`
            );
        }

        if (!response.ok) {
            throw new Error(
                data?.detail ||
                data?.error ||
                `Status request failed (${response.status}).`
            );
        }

        if (data.status === "pending") {

            setGenerationStatus(
                "processing",
                "Queued",
                "Your request is waiting to be processed."
            );

        } else if (data.status === "processing") {

            setGenerationStatus(
                "processing",
                "Generating your video",
                "Creating the script, voiceover and video. This can take a few minutes."
            );

        } else if (data.status === "completed") {

            activeJobId = null;
            clearTimeout(pollTimer);

            showCompletedVideo(data);
            return;

        } else if (data.status === "failed") {

            activeJobId = null;
            clearTimeout(pollTimer);

            setGenerationStatus(
                "error",
                "Generation failed",
                "The video could not be generated.",
                data.error || "Unknown generation error."
            );

            setGeneratingState(false);
            loadDashboard();
            return;

        } else {

            setGenerationStatus(
                "processing",
                "Working...",
                `Job status: ${data.status || "unknown"}`
            );
        }

        pollTimer = setTimeout(
            () => pollJobStatus(jobId),
            GENERATION_POLL_MS
        );

    } catch (error) {

        console.error(
            "Job status error:",
            error
        );

        activeJobId = null;
        clearTimeout(pollTimer);

        setGenerationStatus(
            "error",
            "Unable to check generation",
            "The connection to the server was interrupted.",
            error.message
        );

        setGeneratingState(false);
    }
}

async function startVideoGeneration(event) {
    event.preventDefault();

    if (generationInProgress) {
        return;
    }

    const topicInput =
        document.getElementById("topic");

    const topic =
        topicInput.value.trim();

    if (!topic) {
        topicInput.focus();
        return;
    }

    const duration =
        getSelectedDuration();

    const aspectRatio =
        document.getElementById("aspectRatio").value;

    const voice =
        document.getElementById("voice").value;

    const captions =
        document.getElementById("captions").checked;

    resetVideoResult();

    setGeneratingState(true);

    setGenerationStatus(
        "processing",
        "Starting generation",
        "Submitting your video request..."
    );

    try {

        const response = await fetch(
            `${API_BASE}/generate`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    topic,
                    duration,
                    aspect_ratio: aspectRatio,
                    voice,
                    captions
                })
            }
        );

        let data = null;

        try {
            data = await response.json();
        } catch (_) {
            throw new Error(
                `Invalid server response (${response.status}).`
            );
        }

        if (!response.ok) {

            let message =
                data?.detail ||
                data?.error ||
                `Generation request failed (${response.status}).`;

            if (typeof message === "object") {
                message =
                    message.message ||
                    JSON.stringify(message);
            }

            throw new Error(message);
        }

        if (!data.job_id) {
            throw new Error(
                "The server did not return a job ID."
            );
        }

        activeJobId = data.job_id;

        setGenerationStatus(
            "processing",
            "Generation started",
            "Your video is now being created."
        );

        loadDashboard();

        await pollJobStatus(activeJobId);

    } catch (error) {

        console.error(
            "Generation error:",
            error
        );

        activeJobId = null;

        setGenerationStatus(
            "error",
            "Could not start generation",
            "Your video request was not started.",
            error.message
        );

        setGeneratingState(false);
    }
}

function prepareNewVideo() {
    if (pollTimer) {
        clearTimeout(pollTimer);
    }

    activeJobId = null;
    setGeneratingState(false);
    resetVideoResult();

    document.getElementById(
        "generationStatus"
    ).classList.remove("show");

    document.getElementById(
        "topic"
    ).focus();

    window.scrollTo({
        top: 0,
        behavior: "smooth"
    });
}

document.getElementById(
    "generatorForm"
)?.addEventListener(
    "submit",
    startVideoGeneration
);

document.querySelectorAll(
    'input[name="duration"]'
).forEach(input => {
    input.addEventListener(
        "change",
        updateGenerateHint
    );
});

document.getElementById(
    "newVideoButton"
)?.addEventListener(
    "click",
    prepareNewVideo
);

updateGenerateHint();


/* ==========================================
   START
========================================== */

loadDashboard();


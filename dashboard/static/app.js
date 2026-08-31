const STAGES = [
  { key: "review", label: "Review" },
  { key: "applied", label: "Applied" },
  { key: "research_completed", label: "Research Completed" },
  { key: "screening", label: "Screening" },
  { key: "interview", label: "Interview" },
  { key: "passed", label: "Passed" },
  { key: "skipped", label: "Skipped" },
];

const board = document.getElementById("board");
const jobCountEl = document.getElementById("job-count");
const modalBackdrop = document.getElementById("modal-backdrop");
const jobForm = document.getElementById("job-form");

let jobsById = new Map();

async function fetchJobs() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  jobsById = new Map(data.jobs.map((j) => [j.id, j]));
  render();
}

function render() {
  board.innerHTML = "";
  const jobs = [...jobsById.values()];
  jobCountEl.textContent = `${jobs.length} job${jobs.length === 1 ? "" : "s"} tracked`;

  for (const stage of STAGES) {
    const column = document.createElement("div");
    column.className = "column";

    const stageJobs = jobs.filter((j) => j.stage === stage.key);

    const header = document.createElement("div");
    header.className = "column-header";
    header.style.setProperty("--stage-color", `var(--stage-${stage.key})`);
    header.innerHTML = `<span>${stage.label}</span><span class="column-count">${stageJobs.length}</span>`;
    column.appendChild(header);

    const body = document.createElement("div");
    body.className = "column-body";
    body.dataset.stage = stage.key;

    body.addEventListener("dragover", (e) => {
      e.preventDefault();
      body.classList.add("drag-over");
    });
    body.addEventListener("dragleave", () => body.classList.remove("drag-over"));
    body.addEventListener("drop", async (e) => {
      e.preventDefault();
      body.classList.remove("drag-over");
      const jobId = Number(e.dataTransfer.getData("text/plain"));
      await updateJob(jobId, { stage: stage.key });
      await fetchJobs();
    });

    for (const job of stageJobs) {
      body.appendChild(renderCard(job, stage.key));
    }

    column.appendChild(body);
    board.appendChild(column);
  }
}

function renderCard(job, stageKey) {
  const card = document.createElement("div");
  card.className = "card";
  card.draggable = true;
  card.style.setProperty("--stage-color", `var(--stage-${stageKey})`);
  card.dataset.id = job.id;

  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", String(job.id));
  });
  card.addEventListener("click", () => openModal(job));

  const scoreBadge = job.fit_score != null
    ? `<span class="badge badge-score">${job.fit_score}/10</span>`
    : "";
  const sourceBadge = job.source ? `<span class="badge">${escapeHtml(job.source)}</span>` : "";

  card.innerHTML = `
    <div class="card-title">${escapeHtml(job.title || "Untitled role")}</div>
    <div class="card-company">${escapeHtml(job.company || "")}${job.location ? " · " + escapeHtml(job.location) : ""}</div>
    <div class="card-meta">${scoreBadge}${sourceBadge}</div>
  `;
  return card;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function openModal(job) {
  document.getElementById("job-id").value = job ? job.id : "";
  document.getElementById("field-title").value = job?.title ?? "";
  document.getElementById("field-company").value = job?.company ?? "";
  document.getElementById("field-location").value = job?.location ?? "";
  document.getElementById("field-fit_score").value = job?.fit_score ?? "";
  document.getElementById("field-source").value = job?.source ?? "";
  document.getElementById("field-salary_range").value = job?.salary_range ?? "";
  document.getElementById("field-job_url").value = job?.job_url ?? "";
  document.getElementById("field-score_reasoning").value = job?.score_reasoning ?? "";
  document.getElementById("field-research_notes").value = job?.research_notes ?? "";
  document.getElementById("field-networking_contacts").value = job?.networking_contacts ?? "";
  document.getElementById("field-notes").value = job?.notes ?? "";

  const stageSelect = document.getElementById("field-stage");
  stageSelect.innerHTML = STAGES.map(
    (s) => `<option value="${s.key}">${s.label}</option>`
  ).join("");
  stageSelect.value = job?.stage ?? "review";

  const evidenceBox = document.getElementById("gmail-evidence");
  const evidenceList = document.getElementById("gmail-evidence-list");
  if (job?.gmail_evidence?.length) {
    evidenceBox.classList.remove("hidden");
    evidenceList.innerHTML = job.gmail_evidence.map((e) => `<li>${escapeHtml(e)}</li>`).join("");
  } else {
    evidenceBox.classList.add("hidden");
    evidenceList.innerHTML = "";
  }

  document.getElementById("delete-job-btn").style.display = job ? "inline-block" : "none";

  modalBackdrop.classList.remove("hidden");
}

function closeModal() {
  modalBackdrop.classList.add("hidden");
  jobForm.reset();
}

document.getElementById("add-job-btn").addEventListener("click", () => openModal(null));
document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("cancel-btn").addEventListener("click", closeModal);
modalBackdrop.addEventListener("click", (e) => {
  if (e.target === modalBackdrop) closeModal();
});

document.getElementById("delete-job-btn").addEventListener("click", async () => {
  const id = Number(document.getElementById("job-id").value);
  if (!id) return;
  if (!confirm("Delete this job from the board?")) return;
  await fetch(`/api/jobs/${id}`, { method: "DELETE" });
  closeModal();
  await fetchJobs();
});

jobForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("job-id").value;
  const payload = {
    title: document.getElementById("field-title").value,
    company: document.getElementById("field-company").value,
    location: document.getElementById("field-location").value,
    fit_score: document.getElementById("field-fit_score").value
      ? Number(document.getElementById("field-fit_score").value)
      : null,
    source: document.getElementById("field-source").value,
    salary_range: document.getElementById("field-salary_range").value,
    job_url: document.getElementById("field-job_url").value,
    score_reasoning: document.getElementById("field-score_reasoning").value,
    research_notes: document.getElementById("field-research_notes").value,
    networking_contacts: document.getElementById("field-networking_contacts").value,
    notes: document.getElementById("field-notes").value,
    stage: document.getElementById("field-stage").value,
  };

  if (id) {
    await updateJob(Number(id), payload);
  } else {
    await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }
  closeModal();
  await fetchJobs();
});

async function updateJob(id, payload) {
  await fetch(`/api/jobs/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

const syncBtn = document.getElementById("sync-btn");
const syncStatus = document.getElementById("sync-status");

syncBtn.addEventListener("click", async () => {
  syncBtn.disabled = true;
  syncBtn.textContent = "⟳ Syncing...";
  syncStatus.textContent = "";
  syncStatus.className = "sync-status";
  try {
    const res = await fetch("/api/sync", { method: "POST" });
    const result = await res.json();
    syncStatus.textContent = result.message;
    syncStatus.className = "sync-status " + (result.ok ? "sync-ok" : "sync-error");
    if (result.ok) {
      await fetchJobs();
    }
  } catch (err) {
    syncStatus.textContent = "Sync request failed - is the server still running?";
    syncStatus.className = "sync-status sync-error";
  } finally {
    syncBtn.disabled = false;
    syncBtn.textContent = "⟳ Sync";
  }
});

fetchJobs();

const state = { manifest: null, jobId: null, timer: null };
const $ = (id) => document.getElementById(id);

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

async function loadHealth() {
  try {
    await request("/api/v1/health");
    document.querySelector(".status").classList.add("online");
    $("healthText").textContent = "Studio local actif";
  } catch {
    $("healthText").textContent = "Studio indisponible";
  }
}

async function loadProjects() {
  const data = await request("/api/v1/projects");
  const select = $("manifestSelect");
  select.innerHTML = "";
  data.projects.forEach((project) => {
    const option = document.createElement("option");
    option.value = project.path;
    option.textContent = `${project.title} (${project.duration}s)`;
    select.appendChild(option);
  });
  if (data.projects[0]) {
    state.manifest = data.projects[0].path;
    $("projectTitle").textContent = data.projects[0].title;
    $("projectMeta").textContent = `${data.projects[0].duration} secondes / FR + EN / 16:9 + 9:16`;
  }
}

async function validateProject() {
  const path = $("manifestSelect").value;
  try {
    const data = await request("/api/v1/projects/validate", { method: "POST", body: JSON.stringify({ manifest_path: path }) });
    $("validationScore").textContent = "OK";
    $("projectMeta").textContent = `${data.duration} secondes / ${data.scenes} scenes / ${data.locales.join(" + ").toUpperCase()}`;
  } catch (error) {
    $("validationScore").textContent = "FAIL";
    $("projectMeta").textContent = error.message;
  }
}

async function startRender(event) {
  event.preventDefault();
  const payload = {
    manifest_path: $("manifestSelect").value,
    locale: $("localeSelect").value,
    profile: $("profileSelect").value,
    narration_mode: $("silentCheck").checked ? "silent" : "openai",
  };
  try {
    const job = await request("/api/v1/renders", { method: "POST", body: JSON.stringify(payload) });
    state.jobId = job.id;
    $("jobEmpty").classList.add("hidden");
    $("jobView").classList.remove("hidden");
    $("jobName").textContent = `${payload.locale.toUpperCase()} / ${payload.profile}`;
    $("jobId").textContent = job.id;
    $("cancelButton").classList.remove("hidden");
    pollJob();
  } catch (error) {
    alert(error.message);
  }
}

async function pollJob() {
  if (!state.jobId) return;
  const job = await request(`/api/v1/renders/${state.jobId}`);
  $("jobStatus").textContent = job.status;
  $("jobPercent").textContent = `${job.progress}%`;
  $("progressBar").style.width = `${job.progress}%`;
  if (job.error) {
    $("jobError").textContent = job.error;
    $("jobError").classList.remove("hidden");
  }
  if (["complete", "failed", "cancelled"].includes(job.status)) {
    $("cancelButton").classList.add("hidden");
    if (job.status === "complete") loadArtifacts();
    return;
  }
  state.timer = setTimeout(pollJob, 1000);
}

async function loadArtifacts() {
  const data = await request(`/api/v1/renders/${state.jobId}/artifacts`);
  $("artifactList").innerHTML = data.artifacts.map((item) => `<a href="${item.download}">${item.name}</a>`).join("");
}

async function cancelJob() {
  if (!state.jobId) return;
  await request(`/api/v1/renders/${state.jobId}/cancel`, { method: "POST" });
  pollJob();
}

$("renderForm").addEventListener("submit", startRender);
$("validateButton").addEventListener("click", validateProject);
$("refreshButton").addEventListener("click", () => location.reload());
$("cancelButton").addEventListener("click", cancelJob);
$("manifestSelect").addEventListener("change", (event) => { state.manifest = event.target.value; validateProject(); });
loadHealth();
loadProjects().then(validateProject);

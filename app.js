/**
 * Project M.I.R.A. Integration Dashboard Frontend Controller
 * 
 * Controls UI event listeners, redirects, sync executions, and real-time log polling.
 */

// Connection status tracking
let mondayConnected = false;
let xeroConnected = false;
let pollingInterval = null;
let lastLogIndex = 0;

// DOM Elements
const btnAuthMonday = document.getElementById("btn-auth-monday");
const btnAuthXero = document.getElementById("btn-auth-xero");
const btnSync = document.getElementById("btn-sync");
const btnSaveMapping = document.getElementById("btn-save-mapping");
const mondayStatus = document.getElementById("monday-status");
const xeroStatus = document.getElementById("xero-status");
const terminalBody = document.getElementById("terminal-body");
const syncSpinner = document.getElementById("sync-spinner");
const syncIcon = document.getElementById("sync-icon");
const syncText = document.getElementById("sync-text");

// Initialise dashboard states
document.addEventListener("DOMContentLoaded", () => {
    checkConnectionStatus();
    fetchActiveMappings();
    
    const boardInput = document.getElementById("board-id-1");
    if (boardInput) {
        boardInput.addEventListener("change", (e) => {
            window.loadMondayBoardColumns(e.target.value);
        });
    }
    
    // Poll connection status every 5 seconds to capture callback updates
    setInterval(checkConnectionStatus, 5000);
});

// Monday.com Authorisation Mock Toggle
btnAuthMonday.addEventListener("click", () => {
    mondayConnected = !mondayConnected;
    if (mondayConnected) {
        setConnectedUI("monday", btnAuthMonday, mondayStatus);
        appendLocalLog("[System] Monday.com connection verified and active.", "info");
    } else {
        setDisconnectedUI("monday", btnAuthMonday, mondayStatus);
        appendLocalLog("[System] Monday.com workspace disconnected.", "warning");
    }
});

// Xero Real Authorisation Redirect
btnAuthXero.addEventListener("click", () => {
    if (xeroConnected) {
        // If already connected, simulate disconnection (clear local token)
        if (confirm("Disconnect from Xero Sandbox? This will clear cached authentication credentials.")) {
            fetch("/api/logs", { method: "DELETE" })
                .then(() => {
                    xeroConnected = false;
                    setDisconnectedUI("xero", btnAuthXero, xeroStatus);
                    appendLocalLog("[System] Severed credentials link to Xero.", "warning");
                });
        }
    } else {
        // Redirect to Flask route which triggers Xero OAuth flow
        appendLocalLog("[System] Redirecting browser to Xero OAuth 2.0 gateway...", "info");
        setTimeout(() => {
            window.location.href = "/auth/xero";
        }, 800);
    }
});

// Sync Engine Runner
btnSync.addEventListener("click", () => {
    // Disable button & show loading state
    btnSync.disabled = true;
    btnSync.className = "w-full py-4 bg-indigo-400 text-white font-semibold rounded-xl cursor-not-allowed flex items-center justify-center space-x-2 shadow-lg duration-200";
    syncSpinner.classList.remove("hidden");
    syncIcon.classList.add("hidden");
    syncText.textContent = "Syncing Pipelines...";

    // Clear logs on backend and reset client index
    lastLogIndex = 0;
    terminalBody.innerHTML = "<div>[Terminal console cleared.]</div>";

    appendLocalLog("[System] Triggering asynchronous sync pipeline...", "info");

    const syncDateElem = document.getElementById("input-sync-date");
    const payload = {
        direction: document.getElementById("select-direction").value,
        board_id_1: document.getElementById("board-id-1").value,
        batch_count: parseInt(document.getElementById("input-batch-count").value) || 100,
        group_by_company: document.getElementById("select-mode").value === "true",
        target_account: document.getElementById("select-account").value,
        since_date: syncDateElem && syncDateElem.value ? syncDateElem.value : null
    };

    // Clear logs first
    fetch("/api/logs", { method: "DELETE" })
        .then(() => {
            // Fire API trigger request
            return fetch("/api/run-sync", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });
        })
        .then(response => {
            if (response.status === 202) {
                // Start polling the backend logs
                startLogPolling();
            } else {
                throw new Error(`Server returned HTTP ${response.status}`);
            }
        })
        .catch(error => {
            appendLocalLog(`[System Error] Failed to run sync engine: ${error.message}`, "error");
            resetSyncButton();
        });
});

// Polling logs logic
function startLogPolling() {
    if (pollingInterval) clearInterval(pollingInterval);
    pollingInterval = setInterval(fetchLogs, 500);
}

function stopLogPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
}

function fetchLogs() {
    fetch("/api/logs")
        .then(response => response.json())
        .then(data => {
            const logs = data.logs || [];
            if (logs.length > lastLogIndex) {
                for (let i = lastLogIndex; i < logs.length; i++) {
                    const log = logs[i];
                    appendTerminalLog(log);
                }
                lastLogIndex = logs.length;
            }
            
            // Check if final summary success/failure messages have arrived to stop polling
            const hasSuccess = logs.some(l => l.message.includes("[System Success]") || l.message.includes("complete"));
            const hasFailure = logs.some(l => l.message.includes("failed with error"));
            
            if (hasSuccess || hasFailure) {
                stopLogPolling();
                resetSyncButton();
            }
        })
        .catch(err => {
            console.error("Error polling logs:", err);
        });
}

// Fetch backend connection status
function checkConnectionStatus() {
    fetch("/api/status")
        .then(res => res.json())
        .then(data => {
            // Monday status update
            if (data.monday_connected && !mondayConnected) {
                mondayConnected = true;
                setConnectedUI("monday", btnAuthMonday, mondayStatus);
            }
            // Xero status update
            if (data.xero_connected) {
                xeroConnected = true;
                setConnectedUI("xero", btnAuthXero, xeroStatus);
            } else {
                xeroConnected = false;
                setDisconnectedUI("xero", btnAuthXero, xeroStatus);
            }
        })
        .catch(err => console.error("Error checking connection status:", err));
}

// State variables for interactive schema mapper
window.activeTargetRow = null;
window.showUnmappedOnly = false;
window.sidebarOpen = true;

// Mappings fetcher
window.fetchActiveMappings = function() {
    fetch("/api/config")
        .then(res => res.json())
        .then(data => {
            if (data.board_id_1) {
                document.getElementById("board-id-1").value = data.board_id_1;
                window.loadMondayBoardColumns(data.board_id_1);
            }

            appendLocalLog("[System] Mappings schema configuration loaded successfully.", "info");
        })
        .catch(err => {
            console.error("Error loading mappings:", err);
            appendLocalLog("[System Error] Failed to load schema mappings from server.", "error");
        });
}

// Row selection bindings
window.setupRowSelection = function() {
    const rows = document.querySelectorAll(".target-field-row");
    rows.forEach(row => {
        row.addEventListener("click", () => {
            rows.forEach(r => r.classList.remove("ring-2", "ring-indigo-600", "bg-indigo-50/10"));
            row.classList.add("ring-2", "ring-indigo-600", "bg-indigo-50/10");
            window.activeTargetRow = row;
        });
    });
}

// Interactive UI elements togglers
window.toggleFolder = function(elem) {
    const arrow = elem.querySelector("svg");
    const list = elem.nextElementSibling;
    if (list.classList.contains("hidden")) {
        list.classList.remove("hidden");
        arrow.classList.add("rotate-90");
    } else {
        list.classList.add("hidden");
        arrow.classList.remove("rotate-90");
    }
}

window.toggleSidebar = function() {
    const sidebar = document.getElementById("datastream-sidebar");
    window.sidebarOpen = !window.sidebarOpen;
    if (window.sidebarOpen) {
        sidebar.classList.remove("w-0");
        sidebar.classList.add("w-64");
    } else {
        sidebar.classList.remove("w-64");
        sidebar.classList.add("w-0");
    }
}

// Filter lists functions
window.filterTargets = function() {
    const query = document.getElementById("target-search").value.toLowerCase();
    const rows = document.querySelectorAll(".target-field-row");
    
    rows.forEach(row => {
        const name = row.querySelector(".property-name").textContent.toLowerCase();
        const badge = row.querySelector(".badge-text");
        const badgeVal = badge ? badge.textContent.toLowerCase() : "";
        
        const matchQuery = name.includes(query) || badgeVal.includes(query);
        const matchUnmapped = !window.showUnmappedOnly || !badge;
        
        if (matchQuery && matchUnmapped) {
            row.classList.remove("hidden");
        } else {
            row.classList.add("hidden");
        }
    });
}

window.filterSchemaTree = function() {
    const query = document.getElementById("schema-search").value.toLowerCase();
    const leafNodes = document.querySelectorAll(".schema-leaf-node");
    
    leafNodes.forEach(node => {
        const text = node.textContent.toLowerCase();
        if (text.includes(query)) {
            node.classList.remove("hidden");
            let parent = node.closest("ul");
            while (parent && parent.id !== "schema-tree-container") {
                parent.classList.remove("hidden");
                const arrow = parent.previousElementSibling ? parent.previousElementSibling.querySelector("svg") : null;
                if (arrow) arrow.classList.add("rotate-90");
                parent = parent.parentElement.closest("ul");
            }
        } else {
            node.classList.add("hidden");
        }
    });
}

window.toggleUnmappedOnly = function() {
    window.showUnmappedOnly = !window.showUnmappedOnly;
    const btn = document.getElementById("btn-display-toggle");
    if (window.showUnmappedOnly) {
        btn.textContent = "Display: Unmapped";
    } else {
        btn.textContent = "Display: All";
    }
    window.filterTargets();
}

// Drag & drop handlers
window.handleDragStart = function(event, path) {
    event.dataTransfer.setData("text/plain", path);
    event.dataTransfer.effectAllowed = "copy";
}

window.allowDrop = function(event) {
    event.preventDefault();
}

window.dragEnter = function(event) {
    event.currentTarget.classList.add("border-indigo-500", "bg-indigo-50/20");
}

window.dragLeave = function(event) {
    event.currentTarget.classList.remove("border-indigo-500", "bg-indigo-50/20");
}

window.handleDrop = function(event, element) {
    event.preventDefault();
    element.classList.remove("border-indigo-500", "bg-indigo-50/20");
    const path = event.dataTransfer.getData("text/plain");
    if (path) {
        window.assignMapping(element, path);
    }
}

window.selectSourceNode = function(nodeElem, path) {
    if (window.activeTargetRow) {
        const dz = window.activeTargetRow.querySelector(".dropzone");
        window.assignMapping(dz, path);
    }
}

window.assignMapping = function(dropzoneElem, path) {
    dropzoneElem.innerHTML = `
        <div class="mapping-badge inline-flex items-center px-2 py-0.5 rounded bg-indigo-50 border border-indigo-150 text-indigo-700 font-mono text-[10px] font-medium shadow-2xs select-none">
            <span class="badge-text">${path}</span>
            <button type="button" onclick="clearMapping(event, this)" class="ml-1.5 text-indigo-400 hover:text-indigo-600 focus:outline-none">&times;</button>
        </div>
    `;
}

window.clearMapping = function(event, btn) {
    event.stopPropagation();
    const dropzone = btn.closest(".dropzone");
    dropzone.innerHTML = `<span class="text-[10px] text-slate-400 placeholder-text">Drag field here or click to map...</span>`;
}

// Auto Match target logic
window.autoMatchFields = function() {
    const rows = document.querySelectorAll(".target-field-row");
    const sources = [
        "Invoice.Reference", "Invoice.Contact.Name", "Invoice.Contact.FirstName",
        "Invoice.Contact.LastName", "Invoice.Contact.EmailAddress", "Invoice.Contact.AccountNumber",
        "Invoice.Contact.ContactID", "Invoice.Contact.Address", "Invoice.Contact.Phone",
        "Invoice.LineItems[0].ItemCode", "Invoice.LineItems[0].Description", "Invoice.LineItems[0].Quantity",
        "Invoice.LineItems[0].UnitAmount", "Invoice.LineItems[0].TaxType"
    ];

    rows.forEach(row => {
        const targetName = row.querySelector(".property-name").textContent.trim().toLowerCase();
        const dz = row.querySelector(".dropzone");
        if (dz.querySelector(".mapping-badge")) return;

        let matched = null;
        sources.forEach(src => {
            const parts = src.split(".");
            const last = parts[parts.length - 1].toLowerCase();
            if (targetName === last || 
                targetName.replace(/_/g, "") === last || 
                (targetName === "transaction_id" && last === "reference") ||
                (targetName === "company_name" && last === "name") ||
                (targetName === "unit_price" && last === "unitamount") ||
                (targetName === "item_description" && last === "description")
            ) {
                matched = src;
            }
        });

        if (matched) {
            window.assignMapping(dz, matched);
        }
    });
}

// Add Custom field row
window.addNewFieldRow = function() {
    const name = prompt("Enter target Monday.com column identifier:");
    if (!name) return;
    const cleanName = name.trim().replace(/[^a-zA-Z0-9_]/g, "");
    if (!cleanName) return;

    const tbody = document.getElementById("tbody-mappings");
    const div = document.createElement("div");
    div.className = "target-field-row grid grid-cols-12 bg-white rounded-xl border border-slate-200/80 p-3.5 items-center hover:shadow-xs transition-all duration-150 group cursor-pointer";
    div.setAttribute("data-target-1", cleanName);
    div.setAttribute("data-target-2", "");
    div.setAttribute("data-target-3", "");
    
    const colTitle = formatColTitle(cleanName);
    div.innerHTML = `
        <div class="col-span-5 flex flex-col pr-3">
            <div class="flex items-center space-x-1.5 flex-wrap">
                <span class="text-xs font-bold text-slate-800 column-title">${escapeHtml(colTitle)}</span>
                <span class="text-[10px] font-mono text-slate-400 font-normal property-name">(${escapeHtml(cleanName)})</span>
            </div>
            <span class="text-[9px] text-slate-400 uppercase font-bold tracking-wider mt-0.5">Custom (monday target)</span>
        </div>
        <div class="col-span-7 flex items-center justify-between">
            <div class="dropzone flex-grow min-h-[36px] rounded-lg border border-dashed border-slate-200 bg-slate-50/50 hover:bg-slate-50 hover:border-indigo-400 flex items-center px-3 transition-all"
                 ondragover="allowDrop(event)" ondragenter="dragEnter(event)" ondragleave="dragLeave(event)" ondrop="handleDrop(event, this)">
                <span class="text-[10px] text-slate-400 placeholder-text">Drag field here or click to map...</span>
            </div>
            <div class="ml-2.5 flex items-center space-x-1.5">
                <input type="text" class="override-input w-24 px-2 py-0.5 rounded border border-slate-200 text-[9px] font-mono focus:outline-indigo-600 bg-white" placeholder="Override" value="">
                <button type="button" onclick="removeFieldRow(this)" class="p-0.5 text-slate-400 hover:text-red-500 rounded transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100">
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                    </svg>
                </button>
            </div>
        </div>
    `;
    tbody.appendChild(div);
    window.setupRowSelection();
}

window.removeFieldRow = function(btn) {
    const row = btn.closest(".target-field-row");
    if (row) row.remove();
}

// Mapping settings saver
btnSaveMapping.addEventListener("click", () => {
    const payload = {
        board_id_1: document.getElementById("board-id-1").value,
        mappings: []
    };

    const rows = document.querySelectorAll(".target-field-row");
    rows.forEach(row => {
        const badge = row.querySelector(".badge-text");
        const targetPath = badge ? badge.textContent.trim() : "";
        if (!targetPath) return;

        const b1Col = row.getAttribute("data-target-1");
        const override = row.querySelector(".override-input").value;

        payload.mappings.push({
            target_xero_path: targetPath,
            board_1_col: b1Col,
            custom_override_path: override
        });
    });

    appendLocalLog("[System] Submitting updated mapping schema to server...", "info");

    fetch("/api/config", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "success") {
            appendLocalLog("[System Success] Schema mappings successfully saved to storage.", "info");
            alert("Schema mappings successfully saved!");
        } else {
            appendLocalLog(`[System Validation Error] Failed to save mapping changes.`, "error");
            alert(`Failed to save mapping changes.`);
        }
    })
    .catch(err => {
        console.error("Error saving mappings:", err);
        appendLocalLog("[System Error] Network error occurred while updating mappings.", "error");
    });
});


// Helper formatting UI functions
function setConnectedUI(platform, button, status) {
    status.textContent = "Connected";
    status.className = "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800 animate-none";
    button.textContent = "Disconnect";
    button.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 transition-colors shadow-sm cursor-pointer";
}

function setDisconnectedUI(platform, button, status) {
    status.textContent = "Disconnected";
    status.className = "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800";
    button.textContent = "Authorise";
    button.className = "px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-200 text-slate-700 hover:bg-slate-50 active:bg-slate-100 transition-colors shadow-sm cursor-pointer";
}

function resetSyncButton() {
    btnSync.disabled = false;
    btnSync.className = "w-full py-4 bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white font-semibold rounded-xl transition-all shadow-lg shadow-indigo-600/20 flex items-center justify-center space-x-2 glow-hover duration-200 cursor-pointer";
    syncSpinner.classList.add("hidden");
    syncIcon.classList.remove("hidden");
    syncText.textContent = "Run Sync Engine";
}

function toggleSyncFields() {
    const dir = document.getElementById("select-direction").value;
    const mondayToXeroDiv = document.getElementById("fields-monday-to-xero");
    if (dir === "monday_to_xero") {
        mondayToXeroDiv.classList.remove("hidden");
    } else {
        mondayToXeroDiv.classList.add("hidden");
    }
}

function appendLocalLog(message, type = "info") {
    const timestamp = new Date().toLocaleTimeString();
    let colorClass = "text-emerald-400";
    if (type === "warning") colorClass = "text-amber-400";
    if (type === "error") colorClass = "text-red-400";
    if (type === "stage") colorClass = "text-indigo-400 font-semibold";

    const logLine = document.createElement("div");
    logLine.innerHTML = `<span class="text-slate-500">[${timestamp}]</span> <span class="${colorClass}">${escapeHtml(message)}</span>`;
    terminalBody.appendChild(logLine);
    terminalBody.scrollTop = terminalBody.scrollHeight;
}

function appendTerminalLog(log) {
    const timestampStr = new Date(log.timestamp * 1000).toLocaleTimeString();
    
    let colorClass = "text-emerald-400";
    if (log.level === "WARNING") colorClass = "text-amber-400";
    if (log.level === "ERROR") colorClass = "text-red-400";
    
    // Custom style for stage banners
    if (log.message.startsWith("---")) colorClass = "text-indigo-400 font-semibold";

    const logLine = document.createElement("div");
    logLine.innerHTML = `<span class="text-slate-500">[${timestampStr}]</span> <span class="${colorClass}">${escapeHtml(log.message)}</span>`;
    terminalBody.appendChild(logLine);
    terminalBody.scrollTop = terminalBody.scrollHeight;
}

function clearConsole() {
    terminalBody.innerHTML = "<div>[Terminal console cleared.]</div>";
}

function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

const KNOWN_COL_TITLES = {
    "name": "Name",
    "text_mm663vnh": "Account Number",
    "text_mm66sr6t": "First Name",
    "text_mm668mv4": "Last Name",
    "status": "Status",
    "date4": "Date",
    "text_mm66cg69": "Phone",
    "email_mm5w89d8": "Email",
    "text_mm5wwdh": "Address",
    "text_mm66zhdw": "Xero ID",
    "board_relation_mm67g8vx": "Link to Invoices",
    "text_mm6xnhdy": "Holidays",
    "numeric_mm6631e9": "Invoice Total",
    "numeric_mm66h8ce": "Invoice Total",
    "delivery_address": "Delivery Address",
    "text_mm66zpe0": "Delivery Address",
    "board_relation_mm67qge0": "Contact",
    "board_relation_mm6jp0r9": "Products (Subitem)",
    "numeric_mm66tnmw": "Quantity (Subitem)",
    "numeric_mm662hn4": "Unit Amount (Subitem)",
    "lookup_mm6j8ck6": "Description (Subitem)",
    "item_name": "Item Name",
    "item_number": "Item Code",
    "description": "Description",
    "cost_price": "Cost Price",
    "selling_price": "Selling Price"
};

function formatColTitle(id, rawTitle) {
    if (rawTitle && rawTitle.trim() && rawTitle.trim() !== id) {
        return rawTitle.trim();
    }
    if (id && KNOWN_COL_TITLES[id]) {
        return KNOWN_COL_TITLES[id];
    }
    return id
        .replace(/[_-]+/g, " ")
        .replace(/\b\w/g, c => c.toUpperCase());
}

window.loadMondayBoardColumns = function(boardId) {
    if (!boardId) return;
    
    console.log("Loading columns and details for board ID: " + boardId);
    
    fetch(`/api/mappings/board/${encodeURIComponent(boardId)}/details`)
        .then(res => res.json())
        .then(data => {
            const columns = data.columns || [];
            const currentMappings = data.mappings || [];
            const tbody = document.getElementById("tbody-mappings");
            tbody.innerHTML = ""; // Clear existing rows
            
            columns.forEach(col => {
                const existing = currentMappings.find(m => m.source_column === col.id);
                const mappedPath = existing ? existing.target_xero_path : "";
                const overrideVal = existing ? (existing.custom_override_path || "") : "";
                const colTitle = formatColTitle(col.id, col.title);
                
                const div = document.createElement("div");
                div.className = "target-field-row grid grid-cols-12 bg-white rounded-xl border border-slate-200/80 p-3.5 items-center hover:shadow-xs transition-all duration-150 group cursor-pointer";
                div.setAttribute("data-target-1", col.id);
                div.setAttribute("data-target-2", "");
                div.setAttribute("data-target-3", "");
                
                const typeLabel = col.type || 'text';
                
                div.innerHTML = `
                    <div class="col-span-5 flex flex-col pr-3">
                        <div class="flex items-center space-x-1.5 flex-wrap">
                            <span class="text-xs font-bold text-slate-800 column-title">${escapeHtml(colTitle)}</span>
                            <span class="text-[10px] font-mono text-slate-400 font-normal property-name">(${escapeHtml(col.id)})</span>
                        </div>
                        <span class="text-[9px] text-slate-400 uppercase font-bold tracking-wider mt-0.5">${escapeHtml(typeLabel)} (monday target)</span>
                    </div>
                    <div class="col-span-7 flex items-center justify-between">
                        <div class="dropzone flex-grow min-h-[36px] rounded-lg border border-dashed border-slate-200 bg-slate-50/50 hover:bg-slate-50 hover:border-indigo-400 flex items-center px-3 transition-all"
                             ondragover="allowDrop(event)" ondragenter="dragEnter(event)" ondragleave="dragLeave(event)" ondrop="handleDrop(event, this)">
                            ${mappedPath ? `
                            <div class="mapping-badge inline-flex items-center px-2.5 py-0.5 rounded bg-indigo-50 border border-indigo-150 text-indigo-700 font-mono text-[10px] font-medium shadow-2xs select-none">
                                <span class="badge-text">${mappedPath}</span>
                                <button type="button" onclick="clearMapping(event, this)" class="ml-1.5 text-indigo-400 hover:text-indigo-600 focus:outline-none">&times;</button>
                            </div>` : `
                            <span class="text-[10px] text-slate-400 placeholder-text">Drag field here or click to map...</span>`}
                        </div>
                        <div class="ml-2.5 flex items-center space-x-1.5">
                            <input type="text" class="override-input w-24 px-2 py-0.5 rounded border border-slate-200 text-[9px] font-mono focus:outline-indigo-600 bg-white" placeholder="Override" value="${overrideVal}">
                            <button type="button" onclick="removeFieldRow(this)" class="p-0.5 text-slate-400 hover:text-red-500 rounded transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100">
                                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                                </svg>
                            </button>
                        </div>
                    </div>
                `;
                tbody.appendChild(div);
            });
            
            window.setupRowSelection();
        })
        .catch(err => {
            console.error("Error loading board columns:", err);
        });
}


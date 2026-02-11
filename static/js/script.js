document.addEventListener('DOMContentLoaded', () => {
    init();
});

const API_BASE = "";

let marketData = [];
window.allPairs = [];

async function init() {
    loadMarketData();
    // Default to Dashboard
    window.switchTab('dashboard');
    
    // Polling
    loadDashboardData(); 
}

// --- Navigation ---
window.switchTab = function(tabName) {
    // 1. Hide all views
    document.querySelectorAll('.view-section').forEach(el => el.classList.add('hidden'));
    
    // 2. Show target view
    const target = document.getElementById(`view-${tabName}`);
    if (target) target.classList.remove('hidden');

    // 3. Update Bottom Nav State
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    const navItem = document.getElementById(`nav-${tabName}`);
    if (navItem) navItem.classList.add('active');
}

// --- Data Fetching ---
async function loadMarketData() {
    try {
        const res = await fetch(`${API_BASE}/api/symbols?t=` + Date.now());
        if (!res.ok) throw new Error('Market Data Error');
        window.allPairs = await res.json();
    } catch(e) { 
        console.error(e);
    }
}

// --- Dashboard Logic ---
let pollInterval = 5000;
async function loadDashboardData() {
    try {
        const res = await fetch(`${API_BASE}/api/dashboard?t=` + Date.now());
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await res.json();
        
        // Update Cache
        window.lastBots = data.bots || [];
        
        // 1. Update PnL (Vertical Pill)
        const netPnl = parseFloat(data.financials.net_pnl || 0);
        const pnlEl = document.getElementById('dashTotalPnl');
        if (pnlEl) {
            const sign = netPnl >= 0 ? '+' : '';
            pnlEl.innerText = `${sign}$${netPnl.toFixed(2)}`;
            pnlEl.className = 'pnl-value-main ' + (netPnl >= 0 ? 'text-cyan' : 'text-pink');
        }

        // 2. Update Integrated Coins
        updateIntegratedCoins(data.ticker || {});

        // 3. Update Balance & Bot Count
        const totalBal = parseFloat(data.financials.total_balance || 0);
        const balEl = document.getElementById('totalBal');
        if (balEl) balEl.innerText = '$' + totalBal.toLocaleString(undefined, {minimumFractionDigits: 2});

        const botCount = data.bots ? data.bots.length : 0;
        const countEl = document.getElementById('dashBotCount');
        if (countEl) countEl.innerText = botCount;

        // 4. Update Active Bots (Factory View)
        updateActiveBotsList(data.bots);
        
        // Reset interval on success
        pollInterval = 5000;

    } catch(e) {
        console.warn("Polling failed", e);
        pollInterval = Math.min(pollInterval * 2, 60000);
    } finally {
        setTimeout(loadDashboardData, pollInterval);
    }
}

function updateIntegratedCoins(tickers) {
    const coins = ['BTC', 'ETH', 'SOL', 'XRP', 'BNB'];
    coins.forEach(coin => {
        const pair = `${coin}-USDT`;
        const t = tickers[pair];
        const el = document.getElementById(`price-${coin}`);
        if (el && t) {
            const price = parseFloat(t.last);
            // Format: < $10 use 4 decimals, else 2
            const fmtPrice = price < 10 ? price.toFixed(4) : price.toLocaleString(undefined, {maximumFractionDigits: 2});
            
            el.innerText = `$${fmtPrice}`;
            
            // Color based on change
            const change = parseFloat(t.change);
            el.style.color = change >= 0 ? 'var(--accent-cyan)' : 'var(--accent-pink)';
        }
    });
}

function updateActiveBotsList(bots) {
    const container = document.getElementById('activeBotCards');
    if (!container) return;

    if (!bots || bots.length === 0) {
        container.innerHTML = '<div style="text-align:center; padding:20px; color:#666;">No Active Operations</div>';
        return;
    }

    const html = bots.map(bot => {
        const pnl = parseFloat(bot.pnl || 0);
        const pnlClass = pnl >= 0 ? 'text-cyan' : 'text-pink';
        const sign = pnl >= 0 ? '+' : '';
        const pnlAmt = (bot.investment * (pnl / 100)).toFixed(2);
        
        let uptime = '0m';
        if(bot.start_time) {
            const diff = (new Date() - new Date(bot.start_time)) / 60000;
            const hrs = Math.floor(diff / 60);
            const mins = Math.floor(diff % 60);
            uptime = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
        }

        const so_filled = bot.safety_orders_filled ?? 0;
        const so_max = (bot.dca_config && bot.dca_config.max_safety_orders) || bot.max_safety_orders || 15;

        // Add onclick to view details
        // Note: stopPropagation on buttons to prevent triggering modal when clicking buttons
        return `
        <div class="op-card" onclick="window.viewBotDetails('${bot.symbol}')">
            <div class="op-header">
                <span style="color:#fff;">${bot.symbol}</span>
                <span class="${pnlClass}">${sign}${pnl}% ($${pnlAmt})</span>
            </div>
            <div class="op-details">
                <div>Price: <span class="font-mono text-white">$${parseFloat(bot.current_price).toLocaleString()}</span></div>
                <div>Entry: <span class="font-mono text-white">$${parseFloat(bot.average_entry || 0).toLocaleString()}</span></div>
                <div>Uptime: <span class="font-mono text-white">${uptime}</span></div>
                <div>SO: <span style="color:#a084e8; font-weight:bold;">${so_filled}/${so_max}</span></div>
            </div>
            <div class="op-buttons">
                <button class="btn-panic" onclick="event.stopPropagation(); window.stopBot('${bot.symbol}', 'panic')">PANIC SELL</button>
                <button class="btn-stop" onclick="event.stopPropagation(); window.stopBot('${bot.symbol}', 'stop')">STOP</button>
            </div>
        </div>
        `;
    }).join('');

    // Only update if content changed (rudimentary check to avoid killing scroll/interactions constantly)
    // But since we have onclicks, direct innerHTML replace is safe enough for this scale
    container.innerHTML = html;
}

// --- Factory Logic ---
const searchInput = document.getElementById('factorySearch');
if (searchInput) {
    searchInput.addEventListener('input', (e) => {
        const val = e.target.value.toUpperCase();
        const results = document.getElementById('searchResults');
        
        if (val.length < 2) {
            results.style.display = 'none';
            return;
        }

        const matches = window.allPairs.filter(p => p.symbol.includes(val)).slice(0, 5);
        if (matches.length > 0) {
            results.innerHTML = matches.map(m => `
                <div onclick="window.selectPair('${m.symbol}')" style="padding:12px; border-bottom:1px solid rgba(255,255,255,0.1); color:#fff; cursor:pointer;">
                    ${m.symbol}
                </div>
            `).join('');
            results.style.display = 'block';
        } else {
            results.style.display = 'none';
        }
    });
}

window.selectPair = function(symbol) {
    const display = document.getElementById('factorySelectedDisplay');
    const input = document.getElementById('factorySearch');
    const results = document.getElementById('searchResults');
    
    if(input) input.value = symbol;
    if(display) {
        display.innerText = symbol;
        display.style.display = 'block';
    }
    if(results) results.style.display = 'none';
}

// Shared payload builder
function buildBotPayload(symbol) {
    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value : null;
    };
    
    return {
        symbol: symbol,
        investment: parseFloat(getVal('fBase')),
        dca_config: {
            base_order: parseFloat(getVal('fBase')),
            safety_order: parseFloat(getVal('fSafety')),
            max_safety_orders: parseInt(getVal('fMaxSafety')),
            volume_scale: parseFloat(getVal('fVolScale')),
            step_scale: parseFloat(getVal('fStepScale')),
            price_deviation: parseFloat(getVal('fDev')),
            take_profit: parseFloat(getVal('fTP')),
            tp_type: getVal('fTPType'),
            profit_currency: getVal('fProfitCurrency'), // New
            stop_action: getVal('fStopAction'),
            stop_loss_enabled: true,
            stop_loss: 5.0,
            continuous_mode: getVal('fContinuousMode') === 'true', // New
            entry_type: getVal('fEntryType') // New
        }
    };
}

async function sendBotCreateRequest(payload) {
    try {
        const res = await fetch(`${API_BASE}/api/create_bot`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast('⚡ Strategy Activated: ' + payload.symbol, 'success');
            // Clear search
            document.getElementById('factorySearch').value = '';
            document.getElementById('factorySelectedDisplay').style.display = 'none';
            loadDashboardData();
        } else {
            showToast(data.message || 'Error launching bot', 'error');
        }
    } catch(e) {
        showToast('Connection Error', 'error');
    }
}

window.startVortexStrategy = async function() {
    const input = document.getElementById('factorySearch');
    const symbol = input ? input.value : '';
    
    if (!symbol || symbol.length < 3) {
        showToast('Please select a market first!', 'error');
        return;
    }
    
    // Standard Start uses the configured payload
    const payload = buildBotPayload(symbol);
    await sendBotCreateRequest(payload);
}

window.launchCustomBot = async function() {
    const input = document.getElementById('factorySearch');
    const symbol = input ? input.value : '';
    
    if (!symbol || symbol.length < 3) {
        showToast('Please select a market first!', 'error');
        return;
    }

    // Custom Bot Logic: Explicitly uses the user-defined parameters
    // In this implementation, it's the same as startVortexStrategy because
    // the UI grid is the "Custom" interface. 
    // We can add specific "Custom" flags if backend needs differentiation.
    const payload = buildBotPayload(symbol);
    payload.is_custom = true; // Flag for backend if needed
    
    await sendBotCreateRequest(payload);
}

// Backward compatibility
window.launchFactoryBot = window.startVortexStrategy;

window.stopBot = async function(symbol, action) {
    if (!confirm(`Confirm ${action.toUpperCase()} for ${symbol}?`)) return;
    
    const endpoint = action === 'panic' ? `${API_BASE}/api/panic_sell` : `${API_BASE}/api/stop_bot`;
    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ symbol })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(`${action.toUpperCase()} Executed`, 'success');
            loadDashboardData();
        } else {
            showToast('Failed: ' + data.message, 'error');
        }
    } catch(e) {
        showToast('Network Error', 'error');
    }
}

// --- Bot Details Modal ---
window.viewBotDetails = function(symbol) {
    // Use cached data first
    const bots = window.lastBots || [];
    const bot = bots.find(b => b.symbol === symbol);
    
    if (!bot) return;

    const modal = document.getElementById('botDetailsModal');
    const title = document.getElementById('modalBotSymbol');
    const content = document.getElementById('modalBotContent');
    
    title.innerText = `${bot.symbol} DETAILS`;
    
    const config = bot.dca_config || {};
    
    content.innerHTML = `
        <div class="detail-row"><span class="detail-label">Status</span> <span class="detail-val" style="color:${bot.status === 'active' ? '#03DAC6' : 'orange'}">${bot.status}</span></div>
        <div class="detail-row"><span class="detail-label">PNL</span> <span class="detail-val">${bot.pnl}%</span></div>
        <div class="detail-row"><span class="detail-label">Investment</span> <span class="detail-val">$${bot.investment}</span></div>
        <br>
        <div class="detail-row"><span class="detail-label">Base Order</span> <span class="detail-val">$${config.base_order}</span></div>
        <div class="detail-row"><span class="detail-label">Safety Order</span> <span class="detail-val">$${config.safety_order}</span></div>
        <div class="detail-row"><span class="detail-label">Max Safety</span> <span class="detail-val">${config.max_safety_orders}</span></div>
        <div class="detail-row"><span class="detail-label">Take Profit</span> <span class="detail-val">${config.take_profit}%</span></div>
        <div class="detail-row"><span class="detail-label">Deviation</span> <span class="detail-val">${config.price_deviation}%</span></div>
        <div class="detail-row"><span class="detail-label">Vol Scale</span> <span class="detail-val">${config.volume_scale}</span></div>
        <div class="detail-row"><span class="detail-label">Step Scale</span> <span class="detail-val">${config.step_scale}</span></div>
        <div class="detail-row"><span class="detail-label">Profit Currency</span> <span class="detail-val">${config.profit_currency || 'quote'}</span></div>
        <div class="detail-row"><span class="detail-label">Mode</span> <span class="detail-val">${config.continuous_mode ? 'Loop' : 'One-time'}</span></div>
    `;
    
    modal.classList.remove('hidden');
}

window.loadHistory = async function() {
    try {
        const res = await fetch(`${API_BASE}/api/history?t=` + Date.now());
        if(!res.ok) throw new Error("History fetch failed");
        const history = await res.json();
        const table = history.map(h => 
            `<tr>
                <td>${h.timestamp.replace('T', ' ').split('.')[0]}</td>
                <td>${h.symbol}</td>
                <td>${h.event}</td>
                <td style="color:${h.pnl_percent >= 0 ? 'var(--accent-cyan)' : 'var(--accent-pink)'}">${h.pnl_percent.toFixed(2)}%</td>
                <td style="color:${h.pnl_usd >= 0 ? 'var(--accent-cyan)' : 'var(--accent-pink)'}">$${h.pnl_usd.toFixed(2)}</td>
            </tr>`
        ).join("");
        document.getElementById("history-body").innerHTML = table;
        document.getElementById("historyModal").classList.add("open");
        document.getElementById("historyModal").style.display = 'flex'; // Ensure flex for centering
    } catch (e) {
        console.error(e);
        alert("Failed to load history.");
    }
}

window.closeBotDetails = function() {
    document.getElementById('botDetailsModal').classList.add('hidden');
}

// Close modal on outside click
window.onclick = function(event) {
    const modal = document.getElementById('botDetailsModal');
    if (event.target == modal) {
        modal.classList.add('hidden');
    }
}

function showToast(msg, type) {
    const container = document.getElementById('toastContainer');
    const el = document.createElement('div');
    el.style.background = type === 'success' ? 'var(--accent-cyan)' : 'var(--accent-pink)';
    el.style.color = '#000';
    el.style.padding = '12px 20px';
    el.style.borderRadius = '8px';
    el.style.marginTop = '10px';
    el.style.fontWeight = 'bold';
    el.style.boxShadow = '0 4px 10px rgba(0,0,0,0.3)';
    el.innerText = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}
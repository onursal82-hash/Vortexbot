document.addEventListener('DOMContentLoaded', () => {
    init();
});

let marketData = [];
window.allPairs = [];

async function init() {
    loadMarketData();
    // Default to Dashboard
    window.switchTab('dashboard');
    
    // Polling
    setInterval(loadDashboardData, 2000); 
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
        const res = await fetch('/api/symbols?t=' + Date.now());
        if (!res.ok) throw new Error('Market Data Error');
        window.allPairs = await res.json();
    } catch(e) { 
        console.error(e);
    }
}

// --- Dashboard Logic ---
async function loadDashboardData() {
    try {
        const res = await fetch('/api/dashboard?t=' + Date.now());
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await res.json();
        
        // 1. Update PnL (Vertical Pill)
        const netPnl = parseFloat(data.financials.net_pnl || 0);
        const pnlEl = document.getElementById('dashTotalPnl');
        if (pnlEl) {
            const sign = netPnl >= 0 ? '+' : '';
            pnlEl.innerText = `${sign}$${netPnl.toFixed(2)}`;
            pnlEl.className = 'pnl-value-main ' + (netPnl >= 0 ? 'text-cyan' : 'text-pink');
        }

        // 2. Update Asset Cards (Stacked)
        const assetsContainer = document.getElementById('assets-container');
        if (assetsContainer) {
            const tickers = data.ticker || {};
            // Prioritize specific coins if available, else just top ones
            const displayCoins = ['SOL-USDT', 'BNB-USDT', 'BTC-USDT']; 
            
            const html = displayCoins.map(pair => {
                const t = tickers[pair];
                if (!t) return '';
                const last = parseFloat(t.last).toLocaleString();
                const change = parseFloat(t.change);
                const changeClass = change >= 0 ? 'text-cyan' : 'text-pink';
                const sign = change >= 0 ? '+' : '';
                const symbol = pair.split('-')[0];

                return `
                <div class="asset-item">
                    <div>
                        <div class="asset-symbol">${symbol}</div>
                        <div class="asset-price font-mono">$${last}</div>
                    </div>
                    <div class="asset-change ${changeClass}">${sign}${change.toFixed(2)}%</div>
                </div>
                `;
            }).join('');
            
            // Only update if content changed to prevent flicker (simple check)
            if (assetsContainer.innerHTML.length !== html.length) {
                assetsContainer.innerHTML = html;
            }
        }

        // 3. Update Balance & Bot Count
        const totalBal = parseFloat(data.financials.total_balance || 0);
        const balEl = document.getElementById('totalBal');
        if (balEl) balEl.innerText = '$' + totalBal.toLocaleString(undefined, {minimumFractionDigits: 2});

        const botCount = data.bots ? data.bots.length : 0;
        const countEl = document.getElementById('dashBotCount');
        if (countEl) countEl.innerText = botCount;

        // 4. Update Active Bots (Factory View)
        updateActiveBotsList(data.bots);

    } catch(e) {
        // Silent fail for polling
    }
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
        
        // Calculate Uptime (mock/simple)
        let uptime = '0m';
        if(bot.start_time) {
            const diff = (new Date() - new Date(bot.start_time)) / 60000; // mins
            const hrs = Math.floor(diff / 60);
            const mins = Math.floor(diff % 60);
            uptime = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
        }

        return `
        <div class="op-card">
            <div class="op-header">
                <span style="color:#fff;">${bot.symbol}</span>
                <span class="${pnlClass}">${sign}${pnl}% ($${pnlAmt})</span>
            </div>
            <div class="op-details">
                <div>Price: <span class="font-mono text-white">$${parseFloat(bot.current_price).toLocaleString()}</span></div>
                <div>Uptime: <span class="font-mono text-white">${uptime}</span></div>
                <div>Active SO: <span style="color:#a084e8; font-weight:bold;">${bot.safety_orders_filled}/5</span></div>
                <div>Status: <span style="color:#ccc;">${bot.status}</span></div>
            </div>
            <div class="op-buttons">
                <button class="btn-panic" onclick="window.stopBot('${bot.symbol}', 'panic')">PANIC SELL</button>
                <button class="btn-stop" onclick="window.stopBot('${bot.symbol}', 'stop')">STOP</button>
            </div>
        </div>
        `;
    }).join('');

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

window.startVortexStrategy = async function() {
    const input = document.getElementById('factorySearch');
    const symbol = input ? input.value : '';
    
    if (!symbol || symbol.length < 3) {
        showToast('Please select a market first!', 'error');
        return;
    }

    // Use default hidden values
    const getVal = (id) => document.getElementById(id).value;
    
    const payload = {
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
            profit_currency: getVal('fProfitCurrency'),
            stop_action: getVal('fStopAction'),
            stop_loss_enabled: true,
            stop_loss: 5.0
        }
    };

    try {
        const res = await fetch('/api/create_bot', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast('⚡ Strategy Activated: ' + symbol, 'success');
            // Clear search
            input.value = '';
            document.getElementById('factorySelectedDisplay').style.display = 'none';
            loadDashboardData();
        } else {
            showToast(data.message || 'Error launching bot', 'error');
        }
    } catch(e) {
        showToast('Connection Error', 'error');
    }
}

// Backward compatibility alias just in case
window.launchFactoryBot = window.startVortexStrategy;

window.stopBot = async function(symbol, action) {
    if (!confirm(`Confirm ${action.toUpperCase()} for ${symbol}?`)) return;
    
    const endpoint = action === 'panic' ? '/api/panic_sell' : '/api/stop_bot';
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

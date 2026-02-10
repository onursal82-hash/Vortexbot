document.addEventListener('DOMContentLoaded', () => {
    init();
});

let marketData = [];
window.allPairs = [];

async function init() {
    loadMarketData();
    setInterval(loadDashboardData, 2000); // Real-time 2s updates
    loadDashboardData(); // Initial load
}

// --- Navigation ---
window.switchTab = function(tabName) {
    // Hide all views
    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
    
    // Show selected
    document.getElementById(`view-${tabName}`).classList.add('active');
    
    // Update Tab UI
    const tabs = document.querySelectorAll('.nav-tab');
    if(tabName === 'dashboard') tabs[0].classList.add('active');
    if(tabName === 'factory') tabs[1].classList.add('active');
}

// --- Data Fetching ---
async function loadMarketData() {
    try {
        const res = await fetch('/api/symbols?t=' + new Date().getTime());
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        window.allPairs = data;
        updateTickerFeed(data);
    } catch(e) { 
        console.error("Market Load Error", e); 
        showToast('Failed to load market data', 'error');
    }
}

function updateTickerFeed(data) {
    const feed = document.getElementById('liveMarketFeed');
    if(!feed) return;
    
    const items = data.slice(0, 30).map(coin => {
        const isUp = Math.random() > 0.5; // Mock change
        const changeClass = isUp ? 'ticker-up' : 'ticker-down';
        const arrow = isUp ? '▲' : '▼';
        return `<div class="ticker-item">
            <span>${coin.symbol}</span> 
            <span class="${changeClass}">${arrow} $${parseFloat(coin.last).toLocaleString()}</span>
        </div>`;
    }).join('');
    
    feed.innerHTML = items + items; 
}

// --- Bot Factory Logic ---
const searchInput = document.getElementById('factorySearch');
if(searchInput) {
    searchInput.addEventListener('input', function(e) {
        const val = e.target.value.toUpperCase();
        if(val.length < 2) {
            document.getElementById('searchResults').style.display = 'none';
            return;
        }
        
        // Instant Filter from Cache
        const matches = window.allPairs.filter(c => c.symbol.includes(val)).slice(0, 10);
        
        const resultsBox = document.getElementById('searchResults');
        if(matches.length > 0) {
            resultsBox.innerHTML = matches.map(c => 
                `<div class="search-item" onclick="selectFactoryPair('${c.symbol}')">${c.symbol}</div>`
            ).join('');
            resultsBox.style.display = 'block';
        } else {
            resultsBox.style.display = 'none';
        }
    });
}

window.selectFactoryPair = function(symbol) {
    document.getElementById('factorySelectedDisplay').innerText = symbol;
    document.getElementById('factorySearch').value = symbol;
    document.getElementById('searchResults').style.display = 'none';
    
    const configPanel = document.getElementById('factoryConfig');
    configPanel.style.display = 'block';
}

window.launchFactoryBot = async function() {
    const symbol = document.getElementById('factorySelectedDisplay').innerText;
    if(symbol === '---') return;

    // Gather Full 3Commas Params from Left Panel
    const base = parseFloat(document.getElementById('fBase').value);
    const safety = parseFloat(document.getElementById('fSafety').value);
    const maxSafety = parseInt(document.getElementById('fMaxSafety').value);
    const volScale = parseFloat(document.getElementById('fVolScale').value);
    const stepScale = parseFloat(document.getElementById('fStepScale').value);
    const dev = parseFloat(document.getElementById('fDev').value);
    const tp = parseFloat(document.getElementById('fTP').value);
    
    const tpType = document.getElementById('fTPType').value;
    const profitCurrency = document.getElementById('fProfitCurrency').value;
    const stopAction = document.getElementById('fStopAction').value;

    const payload = {
        symbol: symbol,
        investment: base,
        dca_config: {
            base_order: base,
            safety_order: safety,
            max_safety_orders: maxSafety,
            volume_scale: volScale,
            step_scale: stepScale,
            price_deviation: dev,
            take_profit: tp,
            tp_type: tpType,
            profit_currency: profitCurrency,
            stop_action: stopAction,
            stop_loss_enabled: true,
            stop_loss: 5.0
        }
    };

    console.log('Sending data:', payload);

    try {
        const res = await fetch('/api/create_bot', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        if(data.status === 'success') {
            showToast('Bot Initialized: ' + symbol, 'success');
            
            // Reset Factory UI (Optional)
            document.getElementById('factorySearch').value = '';
            document.getElementById('factorySelectedDisplay').innerText = '---';
            document.getElementById('factoryConfig').style.display = 'none';
            
            // Instant Dashboard Sync
            loadDashboardData();
        } else {
            showToast('Error: ' + data.message, 'error');
        }
    } catch(e) { 
        console.error('Launch Factory Bot Error:', e);
        showToast('Start Failed: ' + e.message, 'error'); 
    }
}

window.stopBot = async function(symbol, action) {
    if(!confirm(`Are you sure you want to ${action.toUpperCase()} ${symbol}?`)) return;
    
    const endpoint = action === 'panic' ? '/api/panic_sell' : '/api/stop_bot';

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ symbol: symbol })
        });
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        if(data.status === 'success') {
            showToast(`Action ${action.toUpperCase()} Successful`, 'success');
            // Instant UI Update (Optimistic)
            loadDashboardData(); 
        } else {
            showToast('Error: ' + (data.message || 'Action Failed'), 'error');
        }
    } catch(e) { 
        console.error('Stop Bot Error:', e); 
        showToast('Network Error: ' + e.message, 'error'); 
    }
}

window.startVortexStrategy = async function() {
    try {
        const symbol = document.getElementById('factorySelectedDisplay').innerText;
        if (!symbol || symbol === '---') {
            showToast('Please search and select a trading pair first.', 'error');
            return;
        }

        // 1. Strategic Transparency: Auto-fill Golden Parameters
        document.getElementById('fBase').value = 20;
        document.getElementById('fSafety').value = 40;
        document.getElementById('fMaxSafety').value = 15;
        document.getElementById('fVolScale').value = 1.05;
        document.getElementById('fStepScale').value = 1.0;
        document.getElementById('fDev').value = 2.0;
        document.getElementById('fTP').value = 1.5;
        
        // Show the config panel so user sees the values
        document.getElementById('factoryConfig').style.display = 'block';

        const payload = { 
            symbol: symbol,
            side: 'buy',
            amount: 20
        };
        console.log('Sending data:', payload);

        const res = await fetch('/api/start_strategy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const errData = await res.json();
            throw new Error(errData.message || `HTTP error! status: ${res.status}`);
        }
        const data = await res.json();
        if(data.status === 'success') {
            showToast('🚀 VORTEX STRATEGY ACTIVATED', 'success');
            loadDashboardData();
        } else {
            showToast('Strategy Error: ' + data.message, 'error');
        }
    } catch(e) {
        console.error('Vortex Strategy Error:', e);
        showToast('Failed to connect to Strategy Engine: ' + e.message, 'error');
    }
}

// --- Dashboard & Active Bots Sync ---
async function loadDashboardData() {
    try {
        // Add timestamp to prevent browser caching
        const res = await fetch('/api/dashboard?t=' + new Date().getTime());
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        
        // 1. Side Asset Cards (Left & Right)
        const tickers = data.ticker || {};
        const topOrder = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT']; // Top 4 for Symmetry
        
        // Update Header Ticker Feed (Real-Time)
        const tickerArray = Object.keys(tickers).map(key => ({
            symbol: key,
            last: tickers[key].last,
            change: tickers[key].change
        }));
        updateTickerFeed(tickerArray);

        const leftContainer = document.getElementById('leftAssetCards');
        const rightContainer = document.getElementById('rightAssetCards');
        
        const renderCard = (sym) => {
            const t = tickers[sym] || {last: 0, change: 0};
            const last = parseFloat(t.last || 0);
            const change = parseFloat(t.change || 0);
            const changeColor = change >= 0 ? 'var(--success)' : 'var(--danger)'; 
            return `
                <div class="market-card mini-card" style="display: flex; justify-content: space-between; align-items: center; padding: 20px;">
                    <div style="text-align: left;">
                        <div class="coin-symbol" style="font-size: 1.1rem; color: #FFD700; font-weight: 800;">${sym.split('-')[0]}</div>
                        <div class="coin-price" style="font-size: 1rem; color: #fff;">$${last.toLocaleString()}</div>
                    </div>
                    <div class="coin-change" style="color: ${changeColor}; font-weight: 800; font-size: 1.1rem;">${change >= 0 ? '+' : ''}${change.toFixed(2)}%</div>
                </div>
            `;
        };

        if(leftContainer) {
            leftContainer.innerHTML = topOrder.slice(0, 2).map(renderCard).join('');
        }
        if(rightContainer) {
            rightContainer.innerHTML = topOrder.slice(2, 4).map(renderCard).join('');
        }
        
        // 2. Financials
        const totalBal = parseFloat(data.financials.total_balance || 0);
        const reserved = parseFloat(data.financials.reserved || 0);
        const netPnl = parseFloat(data.financials.net_pnl || 0);
        
        // Update Bot Count
        const botCount = data.bots ? data.bots.length : 0;
        const botCountEl = document.getElementById('dashBotCount');
        if(botCountEl) botCountEl.innerText = botCount;

        // Update Balance (if element exists, otherwise ignore)
        const totalBalEl = document.getElementById('totalBal');
        if(totalBalEl) totalBalEl.innerText = '$' + totalBal.toLocaleString(undefined, {minimumFractionDigits: 2});

        const reservedEl = document.getElementById('reservedBal');
        if(reservedEl) reservedEl.innerText = '$' + reserved.toLocaleString(undefined, {minimumFractionDigits: 2});
        
        // Update Timestamp
        const tsEl = document.getElementById('lastUpdateTs');
        if(tsEl) {
            const now = new Date();
            tsEl.innerText = now.toLocaleTimeString();
        }

        // Update Circular PnL
        const pnlCircle = document.getElementById('pnlCircle');
        const pnlText = document.getElementById('dashTotalPnl');
        
        if(pnlText) {
            pnlText.textContent = (netPnl >= 0 ? '+' : '') + '$' + netPnl.toLocaleString(undefined, {minimumFractionDigits: 2});
            pnlText.style.fill = netPnl >= 0 ? '#03DAC6' : '#CF6679'; // SVG fill
        }
        
        if(pnlCircle) {
            // Visual Progress based on ROI relative to Balance (scaled for visibility)
            const roi = totalBal > 0 ? (netPnl / totalBal) * 100 : 0;
            const progress = Math.min(Math.abs(roi) * 10, 100); // Scale x10 so small PnL shows up
            
            pnlCircle.setAttribute('stroke-dasharray', `${progress}, 100`);
            pnlCircle.style.stroke = netPnl >= 0 ? '#03DAC6' : '#CF6679';
        }

        // 3. Active Bot Cards (Right Panel in Factory)
        const grid = document.getElementById('activeBotCards');
        if(grid) {
            if(data.bots.length > 0) {
                grid.innerHTML = data.bots.map(bot => {
                    const pnl = bot.pnl || 0;
                    const pnlClass = pnl >= 0 ? 'profit' : 'loss';
                    const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
                    const pnlAmount = (bot.investment * (pnl / 100)).toFixed(2);
                    
                    // Calculate Uptime
                    let uptime = '0m';
                    if(bot.start_time) {
                        const start = new Date(bot.start_time);
                        const now = new Date();
                        const diffMs = now - start;
                        const diffMins = Math.floor(diffMs / 60000);
                        const diffHrs = Math.floor(diffMins / 60);
                        uptime = diffHrs > 0 ? `${diffHrs}h ${diffMins % 60}m` : `${diffMins}m`;
                    }

                    return `
                    <div class="bot-card ${pnlClass}" style="border-left: 4px solid ${pnlColor};">
                        <div class="bot-header">
                            <a href="/api/bot_details/${bot.symbol}" target="_blank" style="color: var(--text-primary); font-size: 1.1rem; text-decoration: none; border-bottom: 1px dotted var(--accent);" title="View Strategy Details">${bot.symbol}</a>
                            <span style="color: ${pnlColor}; font-family: var(--font-mono);">${pnl > 0 ? '+' : ''}${pnl}% ($${pnlAmount})</span>
                        </div>
                        <div class="bot-stats">
                            <span style="color: var(--text-secondary);">Price:</span>
                            <span style="font-family: var(--font-mono);">$${bot.current_price.toLocaleString()}</span>
                        </div>
                        <div class="bot-stats">
                            <span style="color: var(--text-secondary);">Uptime:</span>
                            <span style="font-family: var(--font-mono); color: var(--text-primary);">${uptime}</span>
                        </div>
                        <div class="bot-stats">
                            <span style="color: var(--text-secondary);">Active SO:</span>
                            <span style="color: var(--accent); font-weight: bold;">${bot.safety_orders_filled} / ${bot.dca_config ? bot.dca_config.max_safety_orders : 5}</span>
                        </div>
                        <div class="bot-stats">
                            <span style="color: var(--text-secondary);">Status:</span>
                            <span class="status-badge">${bot.status}</span>
                        </div>
                        <div style="margin-top: 10px; display: flex; gap: 10px;">
                             <button class="btn-panic" onclick="stopBot('${bot.symbol}', 'panic')">PANIC SELL</button>
                             <button class="btn-panic" style="border-color: var(--text-secondary); color: var(--text-secondary);" onclick="stopBot('${bot.symbol}', 'stop')">STOP</button>
                        </div>
                    </div>
                    `;
                }).join('');
            } else {
                grid.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-secondary);">No Active Strategies Running</div>';
            }
        }
        
        // 4. Update Dashboard Main Table (Monitor View)
        const dashTable = document.getElementById('dashboardBotsBody');
        if(dashTable) {
             if(data.bots.length > 0) {
                dashTable.innerHTML = data.bots.map(bot => {
                    const pnl = bot.pnl || 0;
                    const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
                    
                    // Calculate Uptime
                    let uptime = '0m';
                    if(bot.start_time) {
                        const start = new Date(bot.start_time);
                        const now = new Date();
                        const diffMs = now - start;
                        const diffMins = Math.floor(diffMs / 60000);
                        const diffHrs = Math.floor(diffMins / 60);
                        uptime = diffHrs > 0 ? `${diffHrs}h ${diffMins % 60}m` : `${diffMins}m`;
                    }

                    return `
                        <tr>
                            <td style="font-weight: bold; color: var(--text-primary);">${bot.symbol}</td>
                            <td><span class="status-badge">${bot.status}</span></td>
                            <td style="font-family: var(--font-mono);">$${bot.current_price.toLocaleString()}</td>
                            <td style="color: ${pnlColor}; font-weight: bold;">${pnl}%</td>
                            <td style="font-family: var(--font-mono); color: var(--text-secondary);">${uptime}</td>
                            <td>${bot.safety_orders_filled} / ${bot.dca_config ? bot.dca_config.max_safety_orders : 5}</td>
                        </tr>
                    `;
                }).join('');
             } else {
                 dashTable.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 20px; color: var(--text-secondary);">No active bots</td></tr>';
             }
        }
        
        // 5. Update Trade History (Monitor View)
        const histTable = document.getElementById('tradeHistoryBody');
        if(histTable && data.history) {
             if(data.history.length > 0) {
                histTable.innerHTML = data.history.map(tx => {
                    const pnlColor = tx.pnl.includes('+') ? 'var(--success)' : (tx.pnl.includes('-') ? 'var(--danger)' : 'var(--text-secondary)');
                    return `
                        <tr>
                            <td style="font-weight: bold;">${tx.symbol}</td>
                            <td>${tx.type}</td>
                            <td style="font-family: var(--font-mono);">$${tx.price.toLocaleString()}</td>
                            <td style="color: ${pnlColor}; font-weight: bold;">${tx.pnl}</td>
                            <td style="color: var(--text-secondary); font-size: 0.8rem;">${tx.time}</td>
                        </tr>
                    `;
                }).join('');
             } else {
                 histTable.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-secondary);">No trade history available yet.</td></tr>';
             }
        }

    } catch(e) { 
        console.error('Dashboard Load Error:', e); 
        showToast('Failed to load dashboard data: ' + e.message, 'error');
    }
}

// --- Toast System ---
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${message}</span>
        <span style="cursor:pointer; margin-left:10px;" onclick="this.parentElement.remove()">×</span>
    `;

    container.appendChild(toast);

    // Auto remove after 3s
    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

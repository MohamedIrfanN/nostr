document.addEventListener('DOMContentLoaded', () => {
    const userInfoEl = document.getElementById('user-info');
    const statusDot = userInfoEl.querySelector('.status-dot');
    const userIdEl = userInfoEl.querySelector('.user-id');
    const relayStatusBar = document.getElementById('relay-status-bar');
    const eventsFeed = document.getElementById('events-feed');
    const emptyState = document.querySelector('.empty-state');

    // Create a WebSocket connection
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);

    // Track relay statuses
    const relayStatuses = new Map(); // url -> {status, element}

    ws.onopen = () => {
        console.log('Connected to backend WebSocket');
        statusDot.classList.remove('disconnected');
        statusDot.classList.add('connected');
        userIdEl.textContent = 'Listening...';
    };

    ws.onclose = () => {
        console.log('Disconnected from backend WebSocket');
        statusDot.classList.remove('connected');
        statusDot.classList.add('disconnected');
        userIdEl.textContent = 'Disconnected';
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === 'info') {
            handleInfo(msg);
        } else if (msg.type === 'status') {
            handleRelayStatus(msg);
        } else if (msg.type === 'event') {
            handleNewEvent(msg.data);
        } else if (msg.type === 'error') {
            console.error('Error:', msg.message);
            userIdEl.textContent = 'Error';
        }
    };

    function handleInfo(msg) {
        const shortKey = msg.pubkey.slice(0, 8) + '...' + msg.pubkey.slice(-8);
        userIdEl.textContent = shortKey;
        userIdEl.title = msg.pubkey;
    }

    function handleRelayStatus(msg) {
        // msg: {relay, status: 'connected' | 'error', message?}
        let statusObj = relayStatuses.get(msg.relay);

        if (!statusObj) {
            // Create new badge
            const badge = document.createElement('div');
            badge.className = 'relay-badge';

            // Clean relay url for display
            const displayUrl = msg.relay.replace('wss://', '').replace('ws://', '').replace(/\/$/, '');

            badge.innerHTML = `<span>●</span> ${displayUrl}`;
            relayStatusBar.appendChild(badge);

            statusObj = { element: badge };
            relayStatuses.set(msg.relay, statusObj);
        }

        // Update state
        statusObj.element.classList.remove('active', 'error');
        if (msg.status === 'connected') {
            statusObj.element.classList.add('active');
            statusObj.element.title = 'Connected';
        } else if (msg.status === 'error') {
            statusObj.element.classList.add('error');
            statusObj.element.title = msg.message || 'Error connecting';
        }
    }

    function handleNewEvent(eventData) {
        if (emptyState) {
            emptyState.style.display = 'none';
        }

        const card = document.createElement('div');
        card.className = 'event-card';

        const date = new Date(eventData.created_at * 1000);
        const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const relayName = eventData._relay ? eventData._relay.replace('wss://', '').split('/')[0] : 'Unknown Relay';

        // Escape HTML content to prevent XSS (basic)
        const content = eventData.content
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");

        card.innerHTML = `
            <div class="event-header">
                <div class="event-meta">
                    <span class="event-relay">${relayName}</span>
                </div>
                <span class="event-time">${timeStr}</span>
            </div>
            <div class="event-content">${content}</div>
            <div class="event-id">${eventData.id}</div>
        `;

        // Prepend to feed (newest first)
        eventsFeed.insertBefore(card, eventsFeed.firstChild);
    }
});

// MetaMask connection and wallet linking
let account = null;

// Check for user_key in URL params
const urlParams = new URLSearchParams(window.location.search);
const userKeyFromUrl = urlParams.get('user_key');
if (userKeyFromUrl) {
  document.getElementById('userKey').value = userKeyFromUrl;
}

async function connectMetaMask() {
  if (typeof window.ethereum === 'undefined') {
    updateStatus('MetaMask not installed', 'error');
    return;
  }

  try {
    const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
    account = accounts[0];
    updateStatus('Connected', 'success');
    document.getElementById('address').textContent = account;
    document.getElementById('linkBtn').disabled = false;
  } catch (err) {
    updateStatus('Connection failed: ' + err.message, 'error');
  }
}

async function linkWallet() {
  if (!account) {
    updateStatus('Please connect MetaMask first', 'error');
    return;
  }

  const userKey = document.getElementById('userKey').value.trim();
  if (!userKey) {
    updateStatus('Please enter a user key', 'error');
    return;
  }

  try {
    // Get nonce from server
    const nonceRes = await fetch('/api/nonce', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_key: userKey })
    });
    const nonceData = await nonceRes.json();
    if (!nonceData.ok) {
      throw new Error('Failed to get nonce');
    }

    // Build message
    const message = `Link this wallet to user: ${userKey}\nNonce: ${nonceData.nonce}`;

    // Sign message with MetaMask
    const signature = await window.ethereum.request({
      method: 'personal_sign',
      params: [message, account]
    });

    // Send to server
    const linkRes = await fetch('/api/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_key: userKey,
        address: account,
        signature: signature
      })
    });

    const linkData = await linkRes.json();
    if (linkData.ok) {
      updateStatus('Wallet linked successfully!', 'success');
    } else {
      throw new Error(linkData.error || 'Linking failed');
    }
  } catch (err) {
    updateStatus('Linking failed: ' + err.message, 'error');
  }
}

function updateStatus(msg, type) {
  const statusEl = document.getElementById('status');
  statusEl.textContent = msg;
  statusEl.className = type === 'error' ? 'error' : 'success';
}

// Auto-connect if already connected
if (typeof window.ethereum !== 'undefined') {
  window.ethereum.request({ method: 'eth_accounts' })
    .then(accounts => {
      if (accounts.length > 0) {
        account = accounts[0];
        updateStatus('Connected', 'success');
        document.getElementById('address').textContent = account;
        document.getElementById('linkBtn').disabled = false;
      }
    });
}

// Event listeners
document.getElementById('connectBtn').addEventListener('click', connectMetaMask);
document.getElementById('linkBtn').addEventListener('click', linkWallet);

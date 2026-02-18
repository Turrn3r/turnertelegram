// Mobile-first: open MetaMask app on mobile; connect + sign in MetaMask in-app browser or extension
var account = null;

var urlParams = new URLSearchParams(window.location.search);
var userKeyFromUrl = urlParams.get('user_key');
if (userKeyFromUrl) {
  var userKeyEl = document.getElementById('userKey');
  if (userKeyEl) userKeyEl.value = userKeyFromUrl;
}

function isMobile() {
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || (navigator.maxTouchPoints && navigator.maxTouchPoints > 2);
}

function updateStatus(msg, type) {
  var statusEl = document.getElementById('status');
  if (!statusEl) return;
  statusEl.textContent = msg;
  statusEl.className = type === 'error' ? 'error' : 'success';
}

function showConnectFlow() {
  var openBlock = document.getElementById('openInMetaMask');
  var flowBlock = document.getElementById('connectFlow');
  if (openBlock) openBlock.style.display = 'none';
  if (flowBlock) flowBlock.style.display = 'block';
}

function showOpenInMetaMask() {
  var openBlock = document.getElementById('openInMetaMask');
  var flowBlock = document.getElementById('connectFlow');
  if (flowBlock) flowBlock.style.display = 'none';
  if (openBlock) {
    openBlock.style.display = 'block';
    var dappUrl = window.location.href;
    var deepLink = 'https://link.metamask.io/dapp/' + encodeURIComponent(dappUrl);
    var a = document.getElementById('metamaskDeepLink');
    if (a) a.href = deepLink;
  }
}

async function connectMetaMask() {
  if (typeof window.ethereum === 'undefined') {
    if (isMobile()) {
      showOpenInMetaMask();
      return;
    }
    updateStatus('MetaMask not installed', 'error');
    return;
  }

  try {
    var accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
    account = accounts[0];
    updateStatus('Connected', 'success');
    var addrEl = document.getElementById('address');
    if (addrEl) addrEl.textContent = account;
    var linkBtn = document.getElementById('linkBtn');
    if (linkBtn) linkBtn.disabled = false;
  } catch (err) {
    updateStatus('Connection failed: ' + err.message, 'error');
  }
}

async function linkWallet() {
  if (!account) {
    updateStatus('Please connect MetaMask first', 'error');
    return;
  }

  var userKeyEl = document.getElementById('userKey');
  var userKey = userKeyEl ? userKeyEl.value.trim() : '';
  if (!userKey) {
    updateStatus('Please enter a user key', 'error');
    return;
  }

  try {
    var nonceRes = await fetch('/api/nonce', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_key: userKey })
    });
    var nonceData = await nonceRes.json();
    if (!nonceData.ok) throw new Error('Failed to get nonce');

    var message = 'Link this wallet to user: ' + userKey + '\nNonce: ' + nonceData.nonce;
    var signature = await window.ethereum.request({
      method: 'personal_sign',
      params: [message, account]
    });

    var linkRes = await fetch('/api/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_key: userKey,
        address: account,
        signature: signature
      })
    });

    var linkData = await linkRes.json();
    if (linkData.ok) {
      updateStatus('Wallet linked successfully!', 'success');
    } else {
      throw new Error(linkData.error || 'Linking failed');
    }
  } catch (err) {
    updateStatus('Linking failed: ' + err.message, 'error');
  }
}

// On load: if mobile and no ethereum, show "Open in MetaMask"; else show connect flow
if (typeof window.ethereum !== 'undefined') {
  window.ethereum.request({ method: 'eth_accounts' })
    .then(function(accounts) {
      if (accounts.length > 0) {
        account = accounts[0];
        updateStatus('Connected', 'success');
        var addrEl = document.getElementById('address');
        if (addrEl) addrEl.textContent = account;
        var linkBtn = document.getElementById('linkBtn');
        if (linkBtn) linkBtn.disabled = false;
      }
      showConnectFlow();
    })
    .catch(function() { showConnectFlow(); });
} else if (isMobile()) {
  showOpenInMetaMask();
} else {
  showConnectFlow();
}

var connectBtn = document.getElementById('connectBtn');
var linkBtn = document.getElementById('linkBtn');
if (connectBtn) connectBtn.addEventListener('click', connectMetaMask);
if (linkBtn) linkBtn.addEventListener('click', linkWallet);

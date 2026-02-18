// Link wallet to Telegram — works in MetaMask app (mobile) and extension (desktop)
var account = null;
var baseUrl = window.location.origin;

var urlParams = new URLSearchParams(window.location.search);
var userKeyFromUrl = urlParams.get('user_key');
var userKeyEl = document.getElementById('userKey');
var userKeyDisplay = document.getElementById('userKeyDisplay');
if (userKeyFromUrl && userKeyEl) {
  userKeyEl.value = userKeyFromUrl;
  if (userKeyDisplay) userKeyDisplay.textContent = 'Linking to Telegram user: ' + userKeyFromUrl;
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
    var deepLink = 'https://link.metamask.io/dapp/' + encodeURIComponent(window.location.href);
    var a = document.getElementById('metamaskDeepLink');
    if (a) a.href = deepLink;
  }
}

function showSuccess() {
  var block = document.getElementById('successBlock');
  if (block) block.style.display = 'block';
}

async function connectMetaMask() {
  if (typeof window.ethereum === 'undefined') {
    if (isMobile()) { showOpenInMetaMask(); return; }
    updateStatus('MetaMask not installed', 'error');
    return;
  }
  try {
    updateStatus('Connecting…', 'success');
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
  var userKey = userKeyEl ? userKeyEl.value.trim() : '';
  if (!userKey) {
    updateStatus('Missing user key. Open this page from the Telegram bot link.', 'error');
    return;
  }
  try {
    updateStatus('Getting nonce…', 'success');
    var nonceRes = await fetch(baseUrl + '/api/nonce', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_key: userKey })
    });
    var nonceData = await nonceRes.json();
    if (!nonceData.ok) throw new Error('Failed to get nonce');

    var message = 'Link this wallet to user: ' + userKey + '\nNonce: ' + nonceData.nonce;
    updateStatus('Approve the sign request in MetaMask…', 'success');
    var signature = await window.ethereum.request({
      method: 'personal_sign',
      params: [message, account]
    });

    updateStatus('Linking…', 'success');
    var linkRes = await fetch(baseUrl + '/api/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_key: userKey,
        address: account,
        signature: signature
      })
    });

    var linkData = await linkRes.json().catch(function() { return {}; });
    if (linkRes.ok && linkData.ok) {
      updateStatus('Linked!', 'success');
      showSuccess();
      var linkBtn = document.getElementById('linkBtn');
      if (linkBtn) linkBtn.disabled = true;
    } else {
      var errMsg = linkData.detail || linkData.error || (linkRes.status === 400 ? 'Bad request (check signature)' : 'Linking failed');
      if (Array.isArray(linkData.detail)) errMsg = linkData.detail.map(function(d) { return d.msg || d; }).join(', ');
      throw new Error(errMsg);
    }
  } catch (err) {
    var msg = err.message || 'Linking failed';
    if (err.response) msg = msg + ' (check network)';
    updateStatus(msg, 'error');
  }
}

// When opened inside MetaMask: show connect flow and auto-connect if we have user_key
if (typeof window.ethereum !== 'undefined') {
  showConnectFlow();
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
      // If we have user_key and no account yet, auto-trigger connect (one less tap in MetaMask app)
      if (userKeyFromUrl && !account) {
        connectMetaMask();
      }
    })
    .catch(function() {});
} else if (isMobile()) {
  showOpenInMetaMask();
} else {
  showConnectFlow();
}

var connectBtn = document.getElementById('connectBtn');
var linkBtn = document.getElementById('linkBtn');
if (connectBtn) connectBtn.addEventListener('click', connectMetaMask);
if (linkBtn) linkBtn.addEventListener('click', linkWallet);

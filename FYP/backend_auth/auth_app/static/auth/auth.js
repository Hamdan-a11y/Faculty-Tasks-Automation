// Start the Google OAuth flow via backend.
const signInBtn = document.getElementById('googleSignInBtn');
if (signInBtn) {
  signInBtn.addEventListener('click', (event) => {
    event.preventDefault();
    window.location.href = '/auth/google/';
  });
}

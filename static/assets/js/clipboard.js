document.addEventListener('DOMContentLoaded', () => {
    const permalinkBtn = document.getElementById('permalinkBtn');
    if (!permalinkBtn) return; // do nothing if the button isn't on the page

    permalinkBtn.addEventListener('click', function(event) {
        event.preventDefault(); // prevent navigation
        const link = this.href;

        navigator.clipboard.writeText(link)
            .then(() => {
                const originalText = this.textContent;
                this.textContent = 'Copied!';
                setTimeout(() => this.textContent = originalText, 1500);
            })
            .catch(err => console.error('Failed to copy: ', err));
    });
});

const messages = [
  "Reading the chaos…",
  "Identifying recurring sins…",
  "Mapping social damage…",
  "Consulting the elder AI…",
  "This was a lot to take in…",
];

let messageIndex = 0;
const loadingMessageElement = document.getElementById("loadingText");

function updateLoadingMessage() {
  loadingMessageElement.textContent = messages[messageIndex];
  messageIndex = (messageIndex + 1) % messages.length;
}

setInterval(updateLoadingMessage, 3000);
updateLoadingMessage();

import "./styles.css";
import { createTicket, getOrders, getRefunds, getReturns, searchKnowledge, sendChat } from "./api";
import type { KnowledgeArticle, Citation } from "./types";

const app = document.querySelector<HTMLDivElement>("#app")!;

app.innerHTML = `
  <main class="shell">
    <aside class="sidebar" aria-label="Support navigation">
      <header class="brand">
        <div class="brand-mark" aria-hidden="true">K</div>
        <div><strong>Kora Electronics</strong><span>Support Copilot</span></div>
      </header>

      <section class="panel">
        <h2>My activity</h2>
        <div id="account-status" class="account-status"></div>
      </section>
      <section class="panel">
        <h2>Try asking</h2>
        <div id="prompts" class="prompts">
          <button type="button">What is your return policy?</button>
          <button type="button">Do you deliver to Abuja?</button>
          <button type="button">My item arrived damaged.</button>
        </div>
      </section>
    </aside>

    <section class="conversation">
      <header class="conversation-header">
        <div>
          <p class="eyebrow">Kora support</p>
          <h1>How can we help?</h1>
        </div>
        <span class="online"><i></i> Available</span>
      </header>

      <div id="transcript" class="transcript" aria-live="polite"></div>

      <section id="reference-panel" class="reference-panel" aria-labelledby="reference-heading">
        <h2 id="reference-heading">Knowledge reference</h2>
        <p>Select a citation to view its retrieved excerpt.</p>
        <div id="references"></div>
      </section>

      <form id="composer" class="composer">
        <label class="sr-only" for="message">Message</label>
        <textarea id="message" rows="2" maxlength="4000" placeholder="Ask about an order or policy..." required></textarea>
        <div class="composer-actions">
          <button id="escalate" type="button" class="secondary">Escalate to human</button>
          <button id="send" type="submit" class="primary">Send <span aria-hidden="true">↗</span></button>
        </div>
      </form>
      <div id="status" class="sr-only" role="status"></div>
    </section>
  </main>

  <dialog id="ticket-dialog">
    <form id="ticket-form" method="dialog">
      <h2>Create support ticket</h2>
      <p>A mock human-support request will be saved for this conversation.</p>
      <label>Subject<input name="subject" maxlength="200" required /></label>
      <label>Description<textarea name="description" maxlength="4000" required></textarea></label>
      <div class="dialog-actions">
        <button type="button" id="ticket-close" class="secondary">Cancel</button>
        <button type="submit" class="primary">Create ticket</button>
      </div>
    </form>
  </dialog>
`;

const transcript = document.querySelector<HTMLDivElement>("#transcript")!;
const form = document.querySelector<HTMLFormElement>("#composer")!;
const messageInput = document.querySelector<HTMLTextAreaElement>("#message")!;
const sendButton = document.querySelector<HTMLButtonElement>("#send")!;
const status = document.querySelector<HTMLDivElement>("#status")!;
const references = document.querySelector<HTMLDivElement>("#references")!;
const ticketDialog = document.querySelector<HTMLDialogElement>("#ticket-dialog")!;
const ticketForm = document.querySelector<HTMLFormElement>("#ticket-form")!;
const accountStatus = document.querySelector<HTMLDivElement>("#account-status")!;

let userId = localStorage.getItem("support-copilot-user-id");
if (!userId) {
  userId = crypto.randomUUID();
  localStorage.setItem("support-copilot-user-id", userId);
}
const currentUserId = userId;
let sessionId = localStorage.getItem("kora-support-session") || undefined;

function addMessage(role: "user" | "assistant", text: string, citations: Citation[] = []) {
  const row = document.createElement("article");
  row.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "assistant" ? "K" : "You";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  appendResponseContent(bubble, text);

  if (citations.length) {
    const heading = document.createElement("h3");
    heading.textContent = "Sources";
    bubble.append(heading);
    for (const citation of citations) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "citation";
      card.dataset.slug = citation.slug;
      card.textContent = citation.title;
      card.addEventListener("click", () => showReference(citation));
      bubble.append(card);
    }
  }

  row.append(avatar, bubble);
  transcript.append(row);
  transcript.scrollTop = transcript.scrollHeight;
}

function appendResponseContent(bubble: HTMLDivElement, text: string) {
  const lines = text.split(/\r?\n/);
  const products = lines.map(parseProductLine).filter((product): product is ProductResponse => Boolean(product));

  if (products.length >= 2) {
    const firstProduct = lines.findIndex((line) => Boolean(parseProductLine(line)));
    const intro = lines.slice(0, firstProduct).join(" ").trim();
    if (intro) {
      const copy = document.createElement("p");
      copy.textContent = intro;
      bubble.append(copy);
    }

    const heading = document.createElement("div");
    heading.className = "response-heading";
    heading.textContent = "Available products";
    bubble.append(heading);

    const grid = document.createElement("div");
    grid.className = "product-response-grid";
    for (const product of products) {
      const card = document.createElement("article");
      card.className = "product-response-card";
      const name = document.createElement("h4");
      name.textContent = product.name;
      const detail = document.createElement("p");
      detail.textContent = product.detail;
      const stock = document.createElement("span");
      stock.className = "stock-badge";
      stock.textContent = `${product.stock} in stock`;
      card.append(name, detail, stock);
      grid.append(card);
    }
    bubble.append(grid);
    return;
  }

  const list = document.createElement("ul");
  let hasListItems = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (/^[*-]\s+/.test(trimmed)) {
      const item = document.createElement("li");
      appendRichText(item, trimmed.replace(/^[*-]\s+/, ""));
      list.append(item);
      hasListItems = true;
    } else {
      const copy = document.createElement("p");
      appendRichText(copy, trimmed);
      bubble.append(copy);
    }
  }
  if (hasListItems) bubble.append(list);
}

function appendRichText(parent: HTMLElement, text: string) {
  const parts = text.split(/(\*\*.+?\*\*)/g);
  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      parent.append(strong);
    } else {
      parent.append(document.createTextNode(part));
    }
  }
}

interface ProductResponse {
  name: string;
  detail: string;
  stock: string;
}

function parseProductLine(line: string): ProductResponse | undefined {
  const withId = line.match(/^\s*(?:[*-]|•)\s+\*{0,2}(.+?)\*{0,2}\s+\(product_id:\s*[^)]+\):\s*(.+?)\s+-\s+(\d+)\s+in stock\s*$/i);
  const compact = line.match(/^\s*(?:[*-]|•)\s+\*{0,2}(.+?)\*{0,2}:\s*(.+?)\s+\((\d+)\s+in stock\)\s*$/i);
  const match = withId || compact;
  if (!match) return undefined;
  return { name: match[1].trim(), detail: match[2].trim(), stock: match[3] };
}

async function showReference(citation: Citation) {
  references.replaceChildren();
  let article: KnowledgeArticle | undefined;
  try {
    const result = await searchKnowledge(citation.title);
    article = result.articles.find((item) => item.slug === citation.slug);
  } catch {
    // The known citation still opens as a safe, curated title/excerpt reference.
  }
  const card = document.createElement("article");
  card.id = `reference-${citation.slug}`;
  const title = document.createElement("h3");
  title.textContent = citation.title;
  const excerpt = document.createElement("p");
  excerpt.textContent = article?.excerpt || citation.excerpt;
  card.append(title, excerpt);
  references.append(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function initialize() {
  await renderAccountStatus();
  addMessage("assistant", "Hi — I’m your Kora support copilot. Ask me about our policies, delivery, or a sample order.");
}

async function renderAccountStatus() {
  const [orders, returns, refunds] = await Promise.all([getOrders(currentUserId), getReturns(currentUserId), getRefunds(currentUserId)]);
  accountStatus.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = `${orders.length} order${orders.length === 1 ? "" : "s"} · ${returns.length} return${returns.length === 1 ? "" : "s"} · ${refunds.length} refund${refunds.length === 1 ? "" : "s"}`;
  accountStatus.append(summary);
  for (const record of [...orders.slice(0, 3), ...returns.slice(0, 2), ...refunds.slice(0, 2)] as Record<string, unknown>[]) {
    const item = document.createElement("div");
    item.className = "activity-row";
    item.textContent = `${String(record.order_id || record.return_id || record.refund_id)} · ${String(record.fulfillment_status || record.status)}`;
    accountStatus.append(item);
  }
}

document.querySelectorAll<HTMLButtonElement>("#prompts button").forEach((button) => {
  button.addEventListener("click", () => {
    messageInput.value = button.textContent || "";
    messageInput.focus();
  });

});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || sendButton.disabled) return;

  addMessage("user", message);
  messageInput.value = "";
  sendButton.disabled = true;
  status.textContent = /order/i.test(message) ? "Checking order" : "Searching support articles";

  try {
    status.textContent = "Searching support articles";
    const response = await sendChat({ session_id: sessionId, user_id: currentUserId, message });
    sessionId = response.session_id;
    localStorage.setItem("kora-support-session", sessionId);
    status.textContent = "Answering";
    addMessage("assistant", response.answer, response.citations);
  } catch (error) {
    const detail = error instanceof Error ? error.message : "";
    const internal = /gemini|wekoya|bridge|api key|catalogue|configuration|model/i.test(detail);
    addMessage(
      "assistant",
      internal
        ? "The support assistant is temporarily unavailable. Please try again later or escalate to a human agent."
        : detail || "The request failed. Please try again or escalate to a human agent.",
    );
  } finally {
    sendButton.disabled = false;
    status.textContent = "";
    messageInput.focus();
  }
});

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelector<HTMLButtonElement>("#escalate")!.addEventListener("click", () => ticketDialog.showModal());
document.querySelector<HTMLButtonElement>("#ticket-close")!.addEventListener("click", () => ticketDialog.close());

ticketForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(ticketForm);
  try {
    await createTicket({
      user_id: currentUserId,
      subject: String(data.get("subject")),
      description: String(data.get("description")),
    });
    ticketDialog.close();
    ticketForm.reset();
    addMessage("assistant", "Your support ticket was created. A human agent would follow up through this support conversation.");
  } catch (error) {
    addMessage("assistant", error instanceof Error ? error.message : "The ticket could not be created.");
  }
});

initialize().catch(() => {
  addMessage("assistant", "The support service could not be reached. Please ensure the API is running.");
});

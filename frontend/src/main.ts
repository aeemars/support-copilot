import "./styles.css";
import { createTicket, getCustomers, getOrders, searchKnowledge, sendChat } from "./api";
import type { Customer, KnowledgeArticle, Order, ChatResponse, Citation } from "./types";

const app = document.querySelector<HTMLDivElement>("#app")!;

app.innerHTML = `
  <main class="shell">
    <aside class="sidebar" aria-label="Support navigation">
      <header class="brand">
        <div class="brand-mark" aria-hidden="true">K</div>
        <div><strong>Kora Electronics</strong><span>Support Copilot</span></div>
      </header>

      <section class="panel">
        <label for="customer">Demo customer</label>
        <select id="customer" aria-label="Choose demo customer"></select>
      </section>

      <section class="panel">
        <h2>Sample orders</h2>
        <div id="orders" class="order-list"></div>
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
      <p>A mock human-support request will be saved for this demo customer.</p>
      <label>Subject<input name="subject" maxlength="200" required /></label>
      <label>Description<textarea name="description" maxlength="4000" required></textarea></label>
      <div class="dialog-actions">
        <button type="button" id="ticket-close" class="secondary">Cancel</button>
        <button type="submit" class="primary">Create ticket</button>
      </div>
    </form>
  </dialog>
`;

const customerSelect = document.querySelector<HTMLSelectElement>("#customer")!;
const ordersElement = document.querySelector<HTMLDivElement>("#orders")!;
const transcript = document.querySelector<HTMLDivElement>("#transcript")!;
const form = document.querySelector<HTMLFormElement>("#composer")!;
const messageInput = document.querySelector<HTMLTextAreaElement>("#message")!;
const sendButton = document.querySelector<HTMLButtonElement>("#send")!;
const status = document.querySelector<HTMLDivElement>("#status")!;
const references = document.querySelector<HTMLDivElement>("#references")!;
const ticketDialog = document.querySelector<HTMLDialogElement>("#ticket-dialog")!;
const ticketForm = document.querySelector<HTMLFormElement>("#ticket-form")!;

let customers: Customer[] = [];
let selectedCustomer = "";
let sessionId = localStorage.getItem("kora-support-session") || undefined;

function addMessage(role: "user" | "assistant", text: string, citations: Citation[] = []) {
  const row = document.createElement("article");
  row.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "assistant" ? "K" : "You";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const copy = document.createElement("p");
  copy.textContent = text;
  bubble.append(copy);

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

async function renderOrders(customerId: string) {
  const orders: Order[] = await getOrders(customerId);
  ordersElement.replaceChildren();
  for (const order of orders) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "order";
    const id = document.createElement("strong");
    id.textContent = order.order_id;
    const state = document.createElement("span");
    state.textContent = order.status;
    card.append(id, state);
    card.addEventListener("click", () => {
      messageInput.value = `Where is order ${order.order_id}?`;
      messageInput.focus();
    });
    ordersElement.append(card);
  }
}

async function initialize() {
  customers = await getCustomers();
  customerSelect.replaceChildren();
  for (const customer of customers) {
    const option = document.createElement("option");
    option.value = customer.customer_id;
    option.textContent = customer.name;
    customerSelect.append(option);
  }
  selectedCustomer = customers[0]?.customer_id || "";
  await renderOrders(selectedCustomer);
  addMessage("assistant", "Hi — I’m your Kora support copilot. Ask me about our policies, delivery, or a sample order.");
}

customerSelect.addEventListener("change", async () => {
  selectedCustomer = customerSelect.value;
  await renderOrders(selectedCustomer);
});

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
    const response = await sendChat({ session_id: sessionId, customer_id: selectedCustomer, message });
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
      customer_id: selectedCustomer,
      subject: String(data.get("subject")),
      description: String(data.get("description")),
    });
    ticketDialog.close();
    ticketForm.reset();
    addMessage("assistant", "Your support ticket was created. A human agent would follow up through the selected customer’s support channel.");
  } catch (error) {
    addMessage("assistant", error instanceof Error ? error.message : "The ticket could not be created.");
  }
});

initialize().catch(() => {
  addMessage("assistant", "The support service could not be reached. Please ensure the API is running.");
});

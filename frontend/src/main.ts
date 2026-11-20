import "./styles.css";
import { addToCart, checkout, createTicket, getCart, getOrders, getProducts, getRefunds, getReturns, searchKnowledge, sendChat } from "./api";
import type { Cart, KnowledgeArticle, Citation, Product } from "./types";

const app = document.querySelector<HTMLDivElement>("#app")!;

app.innerHTML = `
  <main class="shell">
    <aside class="sidebar" aria-label="Support navigation">
      <header class="brand">
        <div class="brand-mark" aria-hidden="true">K</div>
        <div><strong>Kora Electronics</strong><span>Support Copilot</span></div>
      </header>

      <section class="panel">
        <h2>Shop products</h2>
        <div id="products" class="product-list"></div>
        <button id="cart-toggle" type="button" class="secondary cart-toggle">View cart <span id="cart-count">0</span></button>
      </section>
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

  <dialog id="cart-dialog">
    <form id="checkout-form" method="dialog">
      <h2>Your cart</h2>
      <div id="cart-items"></div>
      <div class="checkout-fields">
        <label>Name<input name="customer_name" required /></label>
        <label>Email<input name="email" type="email" required /></label>
        <label>Address<input name="line1" required /></label>
        <label>City<input name="city" value="Lagos" required /></label>
        <label>State<input name="state" value="Lagos" required /></label>
      </div>
      <div class="dialog-actions">
        <button type="button" id="cart-close" class="secondary">Close</button>
        <button type="submit" class="primary">Place mock order</button>
      </div>
    </form>
  </dialog>

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
const productsElement = document.querySelector<HTMLDivElement>("#products")!;
const cartDialog = document.querySelector<HTMLDialogElement>("#cart-dialog")!;
const checkoutForm = document.querySelector<HTMLFormElement>("#checkout-form")!;
const cartItemsElement = document.querySelector<HTMLDivElement>("#cart-items")!;
const cartCount = document.querySelector<HTMLSpanElement>("#cart-count")!;
const accountStatus = document.querySelector<HTMLDivElement>("#account-status")!;
let products: Product[] = [];
let cart: Cart = { items: [] };

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

async function initialize() {
  products = await getProducts();
  cart = await getCart(currentUserId);
  renderProducts();
  renderCart();
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

function formatPrice(minor: number) {
  return new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN", maximumFractionDigits: 0 }).format(minor / 100);
}

function renderProducts() {
  productsElement.replaceChildren();
  for (const product of products) {
    const card = document.createElement("article");
    card.className = "product-card";
    const name = document.createElement("strong");
    name.textContent = product.name;
    const price = document.createElement("span");
    price.textContent = formatPrice(product.price_minor);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = "Add";
    button.disabled = product.stock_quantity < 1;
    button.addEventListener("click", async () => {
      cart = await addToCart(currentUserId, product.product_id);
      renderCart();
    });
    card.append(name, price, button);
    productsElement.append(card);
  }
}

function renderCart() {
  const count = cart.items.reduce((total, item) => total + item.quantity, 0);
  cartCount.textContent = String(count);
  cartItemsElement.replaceChildren();
  if (!count) {
    const empty = document.createElement("p");
    empty.textContent = "Your cart is empty.";
    cartItemsElement.append(empty);
    return;
  }
  for (const item of cart.items) {
    const product = products.find((entry) => entry.product_id === item.product_id);
    const row = document.createElement("div");
    row.className = "cart-row";
    row.textContent = `${product?.name || item.product_id} × ${item.quantity}`;
    cartItemsElement.append(row);
  }
}

document.querySelectorAll<HTMLButtonElement>("#prompts button").forEach((button) => {
  button.addEventListener("click", () => {
    messageInput.value = button.textContent || "";
    messageInput.focus();
  });

  document.querySelector<HTMLButtonElement>("#cart-toggle")!.addEventListener("click", () => cartDialog.showModal());
  document.querySelector<HTMLButtonElement>("#cart-close")!.addEventListener("click", () => cartDialog.close());

  checkoutForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(checkoutForm);
    try {
      const order = await checkout({
        user_id: currentUserId,
        customer_name: String(data.get("customer_name")),
        email: String(data.get("email")),
        line1: String(data.get("line1")),
        city: String(data.get("city")),
        state: String(data.get("state")),
        payment_method: "mock_card",
      });
      cart = await getCart(currentUserId);
      renderCart();
      await renderAccountStatus();
      cartDialog.close();
      checkoutForm.reset();
      addMessage("assistant", `Order ${order.order_id} was placed successfully. Ask me anytime about its status.`);
    } catch (error) {
      addMessage("assistant", error instanceof Error ? error.message : "The order could not be placed.");
    }
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

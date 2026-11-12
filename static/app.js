'use strict';
const $ = (q, root = document) => root.querySelector(q);
const icon = (name, cls = '') => `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {approved:'Approved', review:'Needs review', rejected:'Rejected', failed:'Failed', processing:'Processing'};
const state = {runs:[], summary:{counts:{},total:0}, procurement:{pos:[],vendors:[]}, samples:[], page:'overview', filter:'all', search:'', run:null, detailTab:'decision', stream:null, busy:false, policy:{match_percent:'1',match_absolute_minor:200}};
const money = (v, currency) => v == null ? '—' : new Intl.NumberFormat('en-US',currency?{style:'currency',currency,minimumFractionDigits:2}:{minimumFractionDigits:2,maximumFractionDigits:2}).format(v/100);
const dateTime = v => v ? new Date(v).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '—';
const shortDate = v => v ? new Date(v).toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '—';
const badge = status => `<span class="badge ${esc(status)}">${esc(labels[status] || status)}</span>`;
let toastTimer, runRefreshTimer;

async function api(path, options={}) {
  const response = await fetch(path, options);
  if (response.status === 401) { location.href='/login'; throw new Error('Please sign in.'); }
  if (!response.ok) {
    let detail = 'Something went wrong. Please try again.';
    try { const data=await response.json(); detail=typeof data.detail==='string' ? data.detail : 'Check the submitted fields and try again.'; } catch {}
    throw new Error(detail);
  }
  return response.json();
}
function toast(message, error=false) {
  clearTimeout(toastTimer); const el=$('#toast'); el.textContent=message;
  el.className=`visible${error?' error':''}`;
  toastTimer=setTimeout(()=>{el.className='';},error?8000:4000);
}
async function refreshData() {
  const [runs,summary,procurement,samples,policy]=await Promise.all([api('/api/runs'),api('/api/summary'),api('/api/procurement'),api('/api/samples'),api('/api/policy')]);
  Object.assign(state,{runs,summary,procurement,samples,policy});
  $('#nav-total').textContent=summary.total;
  $('#nav-review').textContent=summary.counts.review || 0;
}
function heading(title, subtitle, actions='', eyebrow='YOUR AP WORKSPACE') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${subtitle}</p></div>${actions}</div>`;
}
function dateLabel(){return `<div class="date-label">${icon('clock')}${new Date().toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}</div>`;}
function empty(title='Your first decision starts here', text='Upload an invoice or try a sample. Every stage, check, and decision will appear here.', action=true) {
  return `<div class="empty-state"><div class="empty-art">${icon('file')}</div><h3>${title}</h3><p>${text}</p>${action?`<button class="btn" data-action="samples">${icon('play')}Try a sample invoice</button>`:''}</div>`;
}
function tableMarkup(runs, showAll=false){
  return `<div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Vendor</th><th>Amount</th><th>Status / reason</th><th>Received</th><th><span class="subtle">View</span></th></tr></thead><tbody id="table-body">${tableRows(runs)}</tbody></table></div>${!runs.length?`<div id="table-empty">${empty(showAll?'No invoices match this view':'Your first decision starts here',showAll?'Try another filter or upload an invoice.':'Upload an invoice or try a sample. Real decisions will appear here.',!showAll)}</div>`:'<div id="table-empty"></div>'}`;
}
function tableRows(runs) {
  return runs.map(r=>`<tr class="clickable" data-run="${r.id}"><td><div class="invoice-cell"><span class="file-icon">${icon('file')}</span><div><a href="#run/${r.id}"><b>${esc(r.invoice_number || (r.status==='processing'?'Reading invoice…':r.filename))}</b></a><small>${esc(r.filename)}${r.demo?' · Synthetic demo':''}</small></div></div></td><td>${esc(r.vendor || (r.status==='processing'?'Reading document…':'Unidentified vendor'))}</td><td class="table-amount">${money(r.total,r.currency)}<small class="table-muted"> ${esc(r.currency || '')}</small></td><td>${badge(r.status)}<small class="table-reason" title="${esc(r.reason || '')}">${esc(r.reason || 'Processing document…')}</small></td><td class="table-muted">${shortDate(r.created_at)}</td><td>${icon('arrow','tiny')}</td></tr>`).join('');
}
function overview() {
  const c=state.summary.counts;
  const metrics=[['Invoices processed',state.summary.total,'Uploads and isolated sample runs','file',''],['Authorization reserved',c.approved || 0,'Approved; payment not sent','check','green'],['Needs a closer look',c.review || 0,'Waiting for human review','clock','amber'],['Duplicates blocked',state.summary.duplicates || 0,'Same invoice caught early','shield','red']];
  return heading('A little clarity for your day.','Your invoices, their evidence, and the next right step.',dateLabel())+
  `<section class="hero" aria-label="Process an invoice"><div class="hero-copy"><div class="hero-tag">${icon('spark')}A calmer way to manage accounts payable</div><h2>Every invoice.<br>A clear decision.</h2><p>From a messy PDF to a considered decision.<br>All the checks. All the evidence. In one place.</p><div class="hero-bottom">${icon('shield')}Real documents <span>·</span> Explainable outcomes <span>·</span> Human oversight</div></div><div class="upload-section"><div class="dropzone" role="button" tabindex="0" data-action="upload" aria-label="Upload or drop an invoice PDF"><span class="upload-icon">${icon('upload')}</span><b>Drop your invoice here, or <span>browse files</span></b><small>PDF up to 10 MB · Digital or scanned · Up to 12 pages</small></div><button class="sample-link" data-action="samples">No invoice handy? <strong>Try a sample</strong>${icon('arrow')}</button></div></section>`+
  `<section class="metric-grid" aria-label="Invoice metrics">${metrics.map(([label,value,foot,ic,color])=>`<div class="metric ${color}"><div class="metric-label">${label}${icon(ic)}</div><div class="metric-value">${value}</div><div class="metric-foot"><span class="mini-dot"></span>${foot}</div></div>`).join('')}</section>`+
  `<section class="panel"><div class="panel-heading"><div><h2>Recent invoices <span class="run-count">${state.summary.total}</span></h2><p>A running record of what came in and what happened next.</p></div><a class="text-link" href="#invoices">View all invoices ${icon('arrow')}</a></div>${tableMarkup(state.runs.slice(0,5))}<div class="refresh-note"><span>${state.summary.average_ms ? `Average processing time: ${(state.summary.average_ms/1000).toFixed(2)}s` : 'Your workspace is ready for its first invoice.'}</span><span>Policy AP-1.1</span></div></section>`+
  `<section class="process-strip" aria-label="How it works">${[['01','Read the document'],['02','Check the details'],['03','Match the purchase order'],['04','Explain the decision']].map(([n,t],i)=>`${i?icon('arrow'):''}<div class="process-step"><span>${n}</span>${t}</div>`).join('')}</section>`;
}
function filteredRuns() {
  const q=state.search.toLowerCase().trim();
  return state.runs.filter(r=>(state.filter==='all' || r.status===state.filter) && (!q || [r.invoice_number,r.vendor,r.filename,r.po].some(v=>String(v||'').toLowerCase().includes(q))));
}
function invoiceList() {
  const isReview=state.page==='review';
  return heading(isReview?'A human touch, where it matters.':'Every invoice, accounted for.',isReview?'Resolve the uncertainty. Keep the evidence. Make a considered call.':'Explore the history, status, and outputs of your actual runs.',
    `<div class="button-row"><a class="btn" href="/api/export.csv">${icon('down')}Export CSV</a><button class="btn primary" data-action="upload">${icon('upload')}New invoice</button></div>`)+
    (isReview?`<div class="info-banner">${icon('eye')}<div>These invoices need a decision from you. Open an invoice to see the exact issue, the source evidence, and the available next steps.</div></div>`:'')+
    `<section class="panel"><div class="toolbar"><div class="filter-tabs" role="group" aria-label="Filter invoices">${(isReview?[['review','Needs review']]:[['all','All invoices'],['approved','Approved'],['review','Needs review'],['rejected','Rejected'],['failed','Failed']]).map(([v,label])=>`<button class="filter-tab ${state.filter===v?'active':''}" data-filter="${v}" aria-pressed="${state.filter===v}">${label}${v==='all'?` <span class="subtle">${state.summary.total}</span>`:''}</button>`).join('')}</div><div class="search">${icon('search')}<input id="invoice-search" type="search" placeholder="Search invoices or vendors" aria-label="Search invoices or vendors" value="${esc(state.search)}"></div></div>${tableMarkup(filteredRuns(),true)}<div class="refresh-note"><span>Latest ${state.runs.length} submissions · Click an invoice for its full trail</span><button class="sample-link" data-action="samples">Try a sample ${icon('arrow')}</button></div></section>`;
}
function purchaseOrders() {
  return heading('The full picture behind the PO.','Approved amounts, prior commitments, and what’s actually available.',`<button class="btn" data-action="refresh">${icon('layers')}Refresh balances</button>`)+
  `<div class="info-banner">${icon('book')}<div>Amounts include tax. Remaining balance subtracts opening commitments and real uploaded invoices approved in this workspace. Isolated sample reservations appear only in each sample decision. Invoices awaiting review do not reserve funds. Currencies are kept separate.</div></div>`+
  `<div class="po-grid">${state.procurement.pos.map(p=>`<section class="panel po-card"><div class="po-card-top"><h3>${p.id}</h3><span class="badge approved">Open</span></div><p class="po-vendor">${esc(p.vendor)}</p><p class="po-description">${esc(p.description)}</p><div class="po-total">${money(p.remaining,p.currency)} <span class="subtle">${p.currency}</span></div><p class="balance-label">Available to invoice</p><div class="balance-bar" role="img" aria-label="${Math.round((p.opening+p.committed)/p.amount*100)} percent committed"><span class="opening" style="width:${p.opening/p.amount*100}%"></span><span class="committed" style="width:${p.committed/p.amount*100}%"></span></div><div class="po-lines"><span>Approved PO amount</span><b>${money(p.amount,p.currency)}</b></div><div class="po-lines"><span>Opening commitments</span><b>${money(p.opening,p.currency)}</b></div><div class="po-lines"><span>Approved in Ledgerflow</span><b>${money(p.committed,p.currency)}</b></div></section>`).join('')}</div>`;
}
function policy() {
  return heading('Good decisions have clear rules.','A readable policy, applied consistently to every invoice.',`<span class="badge neutral">AP-1.1</span>`)+
  `<div class="policy-grid"><section class="panel policy-card"><h2>${icon('check')}When an invoice is approved</h2><ul><li>Every required field has readable source evidence.</li><li>The date is valid and subtotal + tax matches the total, within one cent.</li><li>The vendor is approved and the invoice is not a duplicate.</li><li>An open PO matches the vendor and currency.</li><li>The total fits within the remaining PO balance. No over-budget allowance.</li><li>Supported line charges reconcile to the net or inclusive total; explicit tax rates reconcile.</li></ul><p>Approval means ready for AP processing. Ledgerflow does not initiate a payment.</p></section><section class="panel policy-card"><h2>${icon('clock')}When a person needs to review</h2><ul><li>A field is missing, conflicting, or uncertain after OCR.</li><li>The invoice does not identify a unique purchase order.</li><li>The amounts do not reconcile, or the date needs correction.</li><li>The invoice exceeds what is still available on the PO.</li></ul><p>A reviewer can confirm an ambiguous PO and verify uncertain OCR. All financial rules are checked again before approval.</p></section><section class="panel policy-card"><h2>${icon('shield')}When an invoice is rejected</h2><ul><li>The same PDF or vendor + invoice number was already submitted.</li><li>The vendor is not approved for procurement.</li><li>The referenced PO belongs to another vendor or is closed.</li><li>The invoice currency differs from the PO currency.</li></ul><p>A reviewer can also reject an invoice with a written reason. The original automated decision remains in the audit trail.</p></section><section class="panel policy-card"><h2>${icon('eye')}Where we ask for certainty</h2><p>English invoices with common field labels. ISO dates (YYYY-MM-DD), decimal-dot amounts, and an explicit currency.</p><div><span class="label">USD</span><span class="label">INR</span><span class="label">EUR</span><span class="label">GBP</span></div><p>Image-only PDFs are read with neural OCR. An OCR token below 90% confidence triggers review. When the Gemini AI reader is enabled, unfamiliar layouts are read by the model — but every value it proposes still passes the same deterministic checks (arithmetic, vendor, duplicate, currency, balance). The model never decides the money. Without it, unsupported layouts are held for review rather than guessed.</p><p>Procurement records and demo vendors are synthetic. No live ERP or email connection is claimed.</p></section><section class="panel policy-card wide"><h2>Three separate limits</h2><p>Arithmetic: one minor unit (0.01), inclusive, for document/line reconciliation. PO line matching: variance must be ≤ both ${esc(state.policy.match_percent)}% of the PO line basis and ${money(state.policy.match_absolute_minor)} currency units. Percentage limits are floored to minor units. Quantity cannot exceed the ordered quantity. Amount comparisons use the ordered amount prorated by the explicitly invoiced quantity when available.</p><p>Matching tolerances never increase spending authorization. The gross invoice must fit within the remaining PO balance exactly. Amount-only POs cannot verify quantities or prices, and split invoices are not compared to the full PO as if it were an invoice.</p></section><section class="panel policy-card wide"><h2>${icon('layers')}Split invoices share one balance</h2><p>A $10,000 PO with $8,000 already committed has $2,000 left. A new $2,450 invoice is held, even though it is smaller than the original PO. Approvals and balance reservations happen together. Pending invoices do not reserve funds, so every later approval checks the latest available balance.</p></section></div>`;
}
const stages=[['intake','Receive invoice'],['read','Read document'],['extract','Extract fields'],['validate','Validate details'],['match','Match purchase order'],['decision','Record decision']];
function timeline(run) {
  return `<aside><section class="panel timeline-panel"><div class="section-kicker">RUN TIMELINE <span class="live-indicator"><span class="status-dot"></span>${run.status==='processing'?'LIVE':'RECORDED'}</span></div><div class="timeline">${stages.map(([key,label],i)=>{const es=run.events.filter(e=>e.stage===key); const last=es.at(-1); const status=run.status!=='processing' && last?.status==='running'?'failed':last?.status || (run.status==='failed'?'skipped':'waiting');return `<div class="timeline-item ${status}"><div class="stage-number">${status==='complete'?icon('check'):status==='failed'?icon('x'):i+1}</div><div><h3>${label}</h3><p>${esc(last?.message || (run.status==='failed'?'Not reached because processing failed.':'Waiting for the previous stage.'))}</p>${last?`<div class="timeline-time">${new Date(last.created_at).toLocaleTimeString('en-US',{hour12:false})}</div>`:''}</div></div>`;}).join('')}</div><div class="timeline-footer"><span>Run ${run.id.slice(0,8)}</span><span>${run.elapsed_ms!=null?`${(run.elapsed_ms/1000).toFixed(2)}s`:run.status==='processing'?'Processing…':'Recorded'}</span></div></section>${run.result?.po?poSnapshot(run.result.po):''}</aside>`;
}
function poSnapshot(p) {
  return `<section class="panel po-snapshot"><h3>${p.id}</h3><p>Balance before this decision</p><div class="balance">${money(p.remaining,p.currency)}</div><p>PO total ${money(p.amount,p.currency)}</p><p>Prior commitments ${money(p.opening+p.committed,p.currency)}</p><p>This invoice ${money(p.invoice_amount,p.currency)}</p><p>Available if approved ${money(p.resulting_available,p.currency)}</p><small>Snapshot at decision time; review reserves nothing. Synthetic samples use isolated balances.</small><a class="text-link" style="margin-top:12px" href="#purchase-orders">View current balances ${icon('arrow')}</a></section>`;
}
function decisionCard(run) {
  const r=run.result;
  const titles={approved:'Ready for accounts payable.',review:'A closer look is needed.',rejected:'Stopped before it moves forward.',failed:'This document couldn’t be processed.',processing:'Following the evidence.'};
  const symbols={approved:'check',review:'eye',rejected:'shield',failed:'alert',processing:'clock'};
  return `<section class="decision-card ${run.status}" aria-live="polite"><div class="decision-title"><span class="decision-symbol">${icon(symbols[run.status] || 'clock')}</span><h2>${titles[run.status] || 'Processing invoice.'}</h2></div><p>${esc(r?.summary || run.error || 'Your real PDF is moving through the workflow. Each completed stage appears in the run timeline.')}</p>${r?`<div class="decision-action">${icon('arrow')}<span>${esc(r.next_action)}</span></div>`:''}${run.status==='processing'?'<div class="loading-bar"></div>':''}</section>`;
}
function decisionTab(run) {
  const r=run.result;
  if(!r) return `<div class="tab-content">${empty(run.status==='failed'?'Try a clearer or valid PDF':'Checks will appear as the document is processed',run.status==='failed'?esc(run.error):'The workflow is inspecting the actual invoice. You can follow it in the timeline.',false)}</div>`;
  return `<div class="tab-content"><div class="check-list">${[...r.checks].sort((a,b)=>(a.status==='pass')-(b.status==='pass')).map(c=>`<div class="check-row"><span class="check-icon ${c.status}">${icon(c.status==='pass'?'check':c.status==='fail'?'x':'alert')}</span><div><h3>${esc(c.label)}</h3><p>${esc(c.detail)}</p></div><span class="badge ${c.status==='pass'?'approved':c.status==='fail'?'rejected':'review'}">${c.status==='pass'?'Passed':c.status==='fail'?'Blocked':'Review'}</span></div>`).join('')}</div>${r.duplicate_run_id?`<div class="report-links"><a href="#run/${r.duplicate_run_id}">Open original invoice →</a></div>`:''}<div class="report-links"><a href="/api/runs/${run.id}/report">Download decision + evidence (JSON)</a>${run.status!=='approved'?`<a href="/api/runs/${run.id}/follow-up">Download vendor follow-up draft</a>`:''}</div></div>`;
}
function fieldsTab(run) {
  const inv=run.invoice;
  if(!inv) return `<div class="tab-content">${empty('Reading your invoice','Extracted fields and source evidence will appear here.',false)}</div>`;
  const fields=[['vendor','Vendor'],['invoice_number','Invoice number'],['invoice_date','Invoice date'],['po_number','PO reference'],['currency','Currency'],['subtotal','Subtotal'],['tax','Tax'],['total','Invoice total']];
  const aiFields=inv.ai_fields || [];
  return `<div class="tab-content"><div class="evidence-layout"><div><div class="field-grid">${fields.map(([key,label])=>`<div class="field"><label>${label}${inv.provenance?.[key]?.method==='derived'?'<span class="ai-chip">Derived</span>':''}${inv.provenance?.[key]?.method==='human correction'?'<span class="ai-chip">Corrected</span>':''}${aiFields.includes(key)?'<span class="ai-chip" title="Read by the AI reader; still validated by deterministic policy">AI</span>':''}</label><b>${inv[key] == null?'Not found — requires review':esc(['subtotal','tax','total'].includes(key)?money(inv[key],inv.currency):inv[key])}</b><span class="evidence">${esc(inv.evidence?.[key] || 'No source evidence present.')} ${inv.provenance?.[key]?.page?` · Page ${inv.provenance[key].page}`:''}${inv.provenance?.[key]?.calculation?`<br>${esc(inv.provenance[key].calculation)}`:''}</span></div>`).join('')}</div>${lineItems(inv)}</div><iframe class="document-frame evidence-document" title="Invoice PDF beside extracted evidence" src="/api/runs/${run.id}/document#toolbar=0"></iframe></div><div class="source-note">${icon('eye','tiny')} ${esc((inv.source.methods || []).join(' + '))} · ${inv.source.pages} page(s)${inv.source.ocr_min_confidence!=null?` · Lowest OCR token: ${Math.round(inv.source.ocr_min_confidence*100)}%`:''}${inv.source.ai_model?` · AI reader: Gemini ${esc(inv.source.ai_model)}`:''}<br>${esc(inv.ai_status || inv.ai_failure || '')}<br>Extraction methods, source pages and calculations are retained. Evidence checks reduce errors; they do not guarantee correct interpretation.${aiFields.length?' Fields marked AI were read by the layout model and accepted only because their quote appears verbatim in the document; the arithmetic, vendor, duplicate, currency, and balance rules still decided this invoice.':''}${(inv.ai_rejected||[]).length?`<br><b>Discarded AI values:</b> ${inv.ai_rejected.map(r=>`${esc(r.field.replace('_',' '))} (${esc(r.reason)})`).join(', ')}. Unsupported candidates require human verification.`:''}</div><details><summary>Read the extracted document text</summary><pre class="raw-text">${esc(inv.raw_text)}</pre></details></div>`;
}
function lineItems(inv) {
  const rows=inv.line_items || [];
  return `<h3 class="line-heading">Charges · ${esc(inv.tax_mode || 'unconfirmed')} tax basis</h3>${rows.length?`<div class="table-wrap"><table><thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th><th>Tax rate / amount</th></tr></thead><tbody>${rows.map(l=>`<tr><td>${esc(l.description)}<small class="evidence">${esc(l.evidence)}</small></td><td>${esc(l.quantity ?? 'Bundle')}</td><td>${money(l.unit_price,inv.currency)}</td><td>${money(l.amount,inv.currency)}</td><td>${esc(l.tax_rate==null?'—':l.tax_rate+'%')} / ${money(l.tax_amount,inv.currency)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="tiny-note">No structured line table could be extracted. See the PO matching scope and any review reasons. Bundle quantities are never invented.</p>'}${Object.keys(inv.conflict_candidates || {}).length?`<details open><summary>Conflicting extraction candidates</summary><pre class="raw-text">${esc(JSON.stringify(inv.conflict_candidates,null,2))}</pre></details>`:''}`;
}
function auditTab(run) {
  return `<div class="tab-content"><div class="tiny-note" style="margin:0 0 18px">Persisted events · All timestamps shown in your local timezone</div>${run.events.map(e=>`<div class="audit-entry"><div class="audit-meta"><span>${esc(e.stage)} · ${esc(e.status)}</span><time>${dateTime(e.created_at)}</time></div><p>${esc(e.message)}</p></div>`).join('')}${run.reviews.length?`<div class="source-note">Human reviews: ${run.reviews.map(r=>`${esc(r.reviewer)} · ${esc(r.action)} · ${esc(r.note)}${r.original_invoice?`<details><summary>Fields before this review</summary><pre class="raw-text">${esc(JSON.stringify(r.original_invoice,null,2))}</pre></details>`:''}`).join('<br>')}<br>Review names are entered by the reviewer; this demo does not verify identity.</div>`:''}<details><summary>Document integrity and policy</summary><pre class="raw-text">SHA-256: ${run.sha256}\nPolicy: ${run.result?.policy_version || 'AP-1.1'}\nReceived: ${run.created_at}</pre></details></div>`;
}
function reviewForm(run) {
  if(run.status!=='review' || !run.events.some(e=>e.stage==='decision')) return '';
  const candidates=run.result?.candidates || [];
  return `<form class="panel review-form" id="review-form" data-run-id="${run.id}"><h3>Your call, with the evidence.</h3><p>Verify the original PDF before submitting. Approval rechecks every policy rule and the current PO balance.</p><div class="form-grid"><div class="form-field"><label for="reviewer">Reviewer name</label><input id="reviewer" name="reviewer" minlength="2" maxlength="80" placeholder="Your name" required autocomplete="name"></div>${candidates.length?`<div class="form-field"><label for="po-id">Confirm the purchase order</label><select id="po-id" name="po_id"><option value="">Select a purchase order</option>${candidates.map(p=>`<option value="${p.id}">${p.id} · ${money(p.amount-p.opening-p.committed,p.currency)} available</option>`).join('')}</select></div>`:''}</div><details class="correction-fields"><summary>Correct extracted date, currency or amounts</summary><p>Enter only fields you verified in the original PDF. A reason is required. Identity and explicit PO changes require a corrected document.</p><div class="form-grid">${[['invoice_date','Invoice date'],['currency','Currency'],['subtotal','Subtotal'],['tax','Tax'],['total','Total']].map(([key,label])=>`<div class="form-field"><label for="correct-${key}">Correct ${label.toLowerCase()}</label><input id="correct-${key}" name="correction_${key}" placeholder="${esc(['subtotal','tax','total'].includes(key)?run.invoice?.[key]!=null?(run.invoice[key]/100).toFixed(2):'0.00':run.invoice?.[key] || label)}"></div>`).join('')}</div><button class="btn" name="action" value="correct" type="submit">Save corrections & recheck</button></details><div class="form-field"><label for="review-note">Decision note</label><textarea id="review-note" name="note" rows="3" minlength="10" maxlength="2000" placeholder="What did you verify, and what supports your decision?" required></textarea></div><div class="button-row"><button class="btn primary" type="submit" name="action" value="approve">${icon('check')}Verify & approve</button><button class="btn danger" type="submit" name="action" value="reject">${icon('x')}Reject with reason</button></div><p class="review-warning tiny-note">Missing fields, inconsistent totals, blocked vendors, duplicates, and insufficient PO balances cannot be waived here. Request a corrected document or procurement update.</p></form>`;
}
function runDetail() {
  const run=state.run;
  if(!run) return '<div class="initial-loading">Loading invoice and evidence…</div>';
  return `<a class="back-link" href="#invoices">${icon('arrow')}Back to invoices</a>`+
    heading(`${esc(run.invoice?.invoice_number || run.filename)} ${badge(run.status)}`,`${esc(run.invoice?.vendor || run.filename)} · Received ${dateTime(run.created_at)}`,
      `<div class="button-row"><a class="btn" href="/api/runs/${run.id}/document" target="_blank" rel="noopener">${icon('file')}Original PDF</a>${run.result?`<a class="btn" href="/api/runs/${run.id}/report">${icon('down')}Export decision</a>`:''}<button class="btn primary" data-action="upload">${icon('upload')}New invoice</button></div>`,'INVOICE WORKSPACE').replace('page-heading','page-heading detail-heading')+
    `${run.demo_scope?'<div class="info-banner">Synthetic demo · This scenario has its own PO balances and duplicate history. You can revisit it safely from Try a sample. No payment is sent.</div>':''}<div class="detail-grid" data-current-run="${run.id}">${timeline(run)}<div>${decisionCard(run)}<section class="panel"><div class="detail-tabs" role="tablist" aria-label="Invoice details">${[['decision','Decision checks'],['fields','Extracted fields'],['document','Original PDF'],['audit','Audit trail']].map(([v,label])=>`<button class="detail-tab ${state.detailTab===v?'active':''}" data-tab="${v}" role="tab" aria-selected="${state.detailTab===v}">${label}</button>`).join('')}</div><div role="tabpanel">${state.detailTab==='decision'?decisionTab(run):state.detailTab==='fields'?fieldsTab(run):state.detailTab==='document'?`<div class="tab-content"><iframe class="document-frame" title="Original uploaded invoice PDF" src="/api/runs/${run.id}/document#toolbar=1"></iframe></div>`:auditTab(run)}</div></section>${reviewForm(run)}</div></div>`;
}
function render() {
  const oldForm=$('#review-form');
  const savedForm=oldForm?{id:oldForm.dataset.runId,values:Object.fromEntries(new FormData(oldForm)),focus:oldForm.contains(document.activeElement)?document.activeElement.name:null,start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd}:null;
  const pages={overview,invoices:invoiceList,review:invoiceList,'purchase-orders':purchaseOrders,policy,run:runDetail};
  $('#main').innerHTML=(pages[state.page] || overview)();
  const newForm=$('#review-form');
  if(savedForm && newForm?.dataset.runId===savedForm.id){
    for(const [name,value] of Object.entries(savedForm.values)){const field=newForm.elements.namedItem(name);if(field)field.value=value;}
    const focused=savedForm.focus?newForm.elements.namedItem(savedForm.focus):null;
    if(focused){focused.focus({preventScroll:true});if(typeof focused.setSelectionRange==='function' && savedForm.start!=null)focused.setSelectionRange(savedForm.start,savedForm.end);}
  }
  document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===state.page || (state.page==='run' && a.dataset.nav==='invoices')));
  const names={overview:'Overview',invoices:'All invoices',review:'Needs review','purchase-orders':'Purchase orders',policy:'Decision policy',run:'Invoice details'};
  $('#page-crumb').textContent=names[state.page] || 'Overview';
  document.title=`${names[state.page] || 'Overview'} · Ledgerflow`;
}
async function route() {
  state.stream?.close(); state.stream=null;
  clearTimeout(runRefreshTimer);
  const [page,id]=(location.hash.slice(1) || 'overview').split('/');
  state.page=['overview','invoices','review','purchase-orders','policy','run'].includes(page)?page:'overview';
  state.search=''; state.filter=state.page==='review'?'review':'all'; state.detailTab='decision'; state.run=null;
  $('#sidebar').classList.remove('open');
  render(); window.scrollTo({top:0,behavior:'instant'});
  if(state.page==='run' && id) {
    try {
      const run=await api(`/api/runs/${encodeURIComponent(id)}`);
      if(state.page!=='run' || location.hash!==`#run/${id}`)return;
      state.run=run; render();
      if(run.status==='processing')connectRun(id);
    } catch(e) {$('#main').innerHTML=`<div class="error-state"><h2>This invoice couldn’t be opened.</h2><p>${esc(e.message)}</p><a class="btn" href="#invoices">Back to invoices</a></div>`;}
  }
}
function connectRun(id) {
  state.stream?.close();
  const stream=new EventSource(`/api/runs/${id}/events`); state.stream=stream;
  async function update() {
    try {const run=await api(`/api/runs/${id}`);if(state.page==='run' && state.run?.id===id){state.run=run;render();}}catch(e){toast(e.message,true);}
  }
  stream.addEventListener('stage',()=>{clearTimeout(runRefreshTimer);runRefreshTimer=setTimeout(update,70);});
  stream.addEventListener('done',async()=>{stream.close();clearTimeout(runRefreshTimer);await update();await refreshData();});
  stream.onerror=()=>{clearTimeout(runRefreshTimer);runRefreshTimer=setTimeout(async()=>{await update();if(state.run?.id===id && state.run.status!=='processing'){stream.close();await refreshData();}},1500);};
}
function modal(header,description,body) {
  $('#modal-content').innerHTML=`<div class="modal-header"><div><h2 id="modal-title">${header}</h2><p>${description}</p></div><button class="icon-button" data-action="close-modal" aria-label="Close dialog">${icon('x')}</button></div><div class="modal-body">${body}</div>`;
  $('#modal').showModal();
}
function showSamples() {
  modal('Real PDFs. Thoughtful edge cases.','Synthetic invoices, real processing. Each run starts an isolated scenario.',`<div class="sample-grid">${state.samples.map(s=>`<article class="sample-card"><div class="sample-category">${esc(s.category)}${badge(s.expected)}</div><h3>${esc(s.title)}</h3><p>${esc(s.description)}</p><div class="button-row"><a class="text-link" href="/api/samples/${s.id}/document">${icon('down','tiny')}PDF</a><button class="btn primary" data-sample="${s.id}">${icon('play')}Run invoice</button></div></article>`).join('')}</div><div class="modal-note">Each sample gets independent PO balances and duplicate history, so every scenario can be revisited. The duplicate scenario processes an original plus a re-export in the same scope. Real repeated uploads still trigger duplicate protection. The AI layout requires a configured, working provider; otherwise it remains in review.</div>`);
}
function showGuide() {
  const steps=[['0:00–1:00 · Explain and approve','Start with the AP problem, then run “A clean invoice.” Show source evidence and the PDF. $12,000 authorized less $4,000 opening commitments gives $8,000; reserving $4,950 leaves $3,050 in this scenario. No payment is sent.'],['1:00–1:30 · Block the re-export','Run “Same invoice. Different PDF.” Its original is processed automatically in the same isolated scenario. Show the different hash and matching vendor/invoice identity; the second reservation is blocked.'],['1:30–2:00 · Respect remaining authorization','Run “The balance tells a different story.” $2,450 exceeds the $2,000 remaining by $450. Procurement authorization or a corrected invoice is needed; a reviewer cannot waive the cap.'],['2:00–2:40 · Reconcile inclusive bundles','Run “Inclusive tax, reconciled charges.” Show the $708 + $472 gross bundles, explicit 18% rate, derived $1,000 net and $180 tax. Quantities are not invented; the amount-only PO limitation is visible.'],['2:40–3:30 · Resolve PO ambiguity','Run “Two possible purchase orders.” Select PO-2026-1048, enter a reviewer name and evidence-based reason, then approve. Show the $990 reservation and original review outcome in the audit trail.'],['3:30–4:15 · Read a layout the rules can’t','Run “A layout the labels miss.” Label rules read nothing, so Gemini reads it. Open Extracted fields: each AI-tagged value carries a verbatim quote that the code verified in the PDF, and the same deterministic policy approves it. Gemini is only called when rules leave fields unread, so the other invoices never depend on it.'],['4:15–4:40 · Show operations and scope','Open All invoices and Purchase orders. Explain the reasoned handoff, synthetic procurement, limited table layouts and self-reported reviewer names. No accuracy or savings figures have been measured.']];
  modal('A five-minute story, with evidence.','Suggested narration: 4:40, leaving time for clicks.',steps.map(([h,p],i)=>`<div class="guide-step"><span class="num">${i+1}</span><div><h3>${h}</h3><p>${p}</p></div></div>`).join('')+`<div class="modal-note">Each sample is independently repeatable. Actual uploads share the main authorization and duplicate register. The full narrated script is in DEMO_SCRIPT.md in the project.</div>`);
}
async function startRun(action) {
  if(state.busy)return;
  state.busy=true;
  document.querySelectorAll('[data-sample]').forEach(b=>b.disabled=true);
  try {
    const result=await action();
    if($('#modal').open)$('#modal').close();
    await refreshData();
    location.hash=`run/${result.id}`;
    toast('Invoice received. Follow the evidence as it runs.');
  }catch(e){toast(e.message,true);}finally{state.busy=false;document.querySelectorAll('[data-sample]').forEach(b=>b.disabled=false);}
}
function uploadFile(file) {
  if(!file)return;
  if(file.size>10*1024*1024){toast('Choose a PDF smaller than 10 MB.',true);return;}
  if(!/\.pdf$/i.test(file.name) && file.type!=='application/pdf'){toast('Choose a PDF invoice.',true);return;}
  const data=new FormData();data.append('file',file);
  startRun(()=>api('/api/runs',{method:'POST',body:data}));
}
document.addEventListener('click',async e=>{
  const sample=e.target.closest('[data-sample]');
  if(sample){await startRun(()=>api(`/api/samples/${sample.dataset.sample}/run`,{method:'POST'}));return;}
  const filter=e.target.closest('[data-filter]');
  if(filter){state.filter=filter.dataset.filter;render();return;}
  const tab=e.target.closest('[data-tab]');
  if(tab){state.detailTab=tab.dataset.tab;render();return;}
  const action=e.target.closest('[data-action]')?.dataset.action;
  if(action==='upload')$('#file-input').click();
  if(action==='samples')showSamples();
  if(action==='guide')showGuide();
  if(action==='close-modal')$('#modal').close();
  if(action==='menu')$('#sidebar').classList.toggle('open');
  if(action==='refresh'){try{await refreshData();render();toast('Balances refreshed.');}catch(err){toast(err.message,true);}}
  const row=e.target.closest('[data-run]');
  if(row && !e.target.closest('a'))location.hash=`run/${row.dataset.run}`;
});
document.addEventListener('keydown',e=>{
  if((e.key==='Enter' || e.key===' ') && e.target.matches('.dropzone')){e.preventDefault();$('#file-input').click();}
});
$('#file-input').addEventListener('change',e=>{uploadFile(e.target.files[0]);e.target.value='';});
document.addEventListener('dragover',e=>{const zone=e.target.closest('.dropzone');if(zone){e.preventDefault();zone.classList.add('drag');}});
document.addEventListener('dragleave',e=>{const zone=e.target.closest('.dropzone');if(zone && !zone.contains(e.relatedTarget))zone.classList.remove('drag');});
document.addEventListener('drop',e=>{const zone=e.target.closest('.dropzone');if(zone){e.preventDefault();zone.classList.remove('drag');uploadFile(e.dataTransfer.files[0]);}});
document.addEventListener('input',e=>{
  if(e.target.id==='invoice-search'){
    state.search=e.target.value;const runs=filteredRuns();$('#table-body').innerHTML=tableRows(runs);
    $('#table-empty').innerHTML=runs.length?'':empty('No invoices match this view','Try another search or filter.',false);
  }
});
document.addEventListener('submit',async e=>{
  if(e.target.id!=='review-form')return;
  e.preventDefault();const form=e.target;const id=form.dataset.runId;const data=Object.fromEntries(new FormData(form));
  data.action=e.submitter?.value || 'approve';
  if(!data.po_id)delete data.po_id;
  data.corrections={};
  for(const key of Object.keys(data)){if(key.startsWith('correction_')){if(data[key].trim())data.corrections[key.slice(11)]=data[key].trim();delete data[key];}}
  form.querySelectorAll('button').forEach(b=>b.disabled=true);
  try {
    const run=await api(`/api/runs/${id}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(state.run?.id===id)state.run=run;
    await refreshData();render();toast('Review saved. Decision and balances updated.');
  } catch(err){toast(err.message,true);form.querySelectorAll('button').forEach(b=>b.disabled=false);}
});
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom)e.target.close();}});
window.addEventListener('hashchange',route);
async function boot() {
  try {
    await api('/api/health');$('#health').innerHTML='<span class="status-dot"></span>Processor online';
    await refreshData();await route();
  }catch(e){$('#health').textContent='Connection unavailable';$('#main').innerHTML=`<div class="error-state"><h2>The workspace couldn’t connect.</h2><p>${esc(e.message)}</p><button class="btn" onclick="location.reload()">Try again</button></div>`;}
}
setInterval(async()=>{
  if(state.page==='run' || document.hidden)return;
  try{const hadProcessing=state.runs.some(r=>r.status==='processing');await refreshData();if(hadProcessing || state.runs.some(r=>r.status==='processing'))render();}catch{}
},4000);
boot();

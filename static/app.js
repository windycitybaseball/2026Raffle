(function () {
  const radios = document.querySelectorAll('input[name="tier_id"]');
  const total = document.querySelector('[data-total]');
  function update() {
    const selected = document.querySelector('input[name="tier_id"]:checked');
    if (selected && total) {
      total.textContent = (Number(selected.dataset.price) / 100).toLocaleString('en-US', {style: 'currency', currency: 'USD'});
    }
  }
  radios.forEach(radio => radio.addEventListener('change', update));
  update();
})();

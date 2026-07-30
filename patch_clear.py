import sys

with open('pageobjects/pbi_dashboard_page.py', 'r') as f:
    lines = f.readlines()

new_method = """
    def clear_all_slicers(self) -> None:
        '''
        Attempts to clear all slicers on the page by finding and clicking 
        all visible 'Eraser' (clear) icons in slicer headers.
        '''
        log.info("Attempting to clear all active slicers on the page...")
        try:
            # Look for eraser icons
            clear_icons = self.page.locator("i[class*='clear'], i[class*='eraser']")
            total_icons = clear_icons.count()
            count = 0
            for i in range(total_icons):
                icon = clear_icons.nth(i)
                if icon.is_visible(timeout=500):
                    icon.click(force=True)
                    self.page.wait_for_timeout(500)
                    count += 1
            log.info(f"Cleared {count} active slicer(s).")
            
            # Also attempt global reset button if available
            reset_btn = self.page.locator("button[aria-label='Reset to default'], button[title='Reset to default']").first
            if reset_btn.is_visible(timeout=500):
                reset_btn.click()
                log.info("Clicked global 'Reset to default' button.")
                self.page.wait_for_timeout(2000)
        except Exception as e:
            log.warning(f"Error while clearing slicers: {e}")

"""

# Insert before `def reset_slicer`
for i, line in enumerate(lines):
    if "def reset_slicer(self, slicer_title" in line:
        lines.insert(i, new_method)
        break

with open('pageobjects/pbi_dashboard_page.py', 'w') as f:
    f.writelines(lines)

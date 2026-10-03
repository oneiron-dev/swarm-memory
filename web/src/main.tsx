import { render } from 'preact'
import { App } from './app'
import './styles/app.css'

// ?theme=light|dark forces a room (screenshots, testing); otherwise the system decides.
const theme = new URLSearchParams(location.search).get('theme')
if (theme === 'light' || theme === 'dark') document.documentElement.classList.add(theme)

render(<App />, document.getElementById('app')!)

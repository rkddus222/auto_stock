/** @type {import('tailwindcss').Config} */
import preset from 'tailwindcss/preset'

export default {
  presets: [preset],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
}

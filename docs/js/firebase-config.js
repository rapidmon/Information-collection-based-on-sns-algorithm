// Firebase Web SDK v10 (ES Module via CDN)
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getFirestore } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

const firebaseConfig = {
    apiKey: "AIzaSyAsnsfkBLYBCQNJZXCJ-OM-Kj9SICBYfKA",
    authDomain: "sns-algorithm-13b18.firebaseapp.com",
    projectId: "sns-algorithm-13b18",
    storageBucket: "sns-algorithm-13b18.firebasestorage.app",
    messagingSenderId: "456608502362",
    appId: "1:456608502362:web:68e077ec328d68e55e3e63",
    measurementId: "G-9SVX37LWED"
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

export { db };

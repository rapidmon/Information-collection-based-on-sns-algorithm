// Firebase Web SDK v10 (ES Module via CDN)
import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getFirestore } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js';

// TODO: Firebase Console → 프로젝트 설정 → 웹 앱 추가 → config 값으로 교체하세요
const firebaseConfig = {
    apiKey: "AIzaSyBYptU_FU31Ku8uiT1SW0kwKruq9ezKxWk",
    authDomain: "sns-algorithm.firebaseapp.com",
    projectId: "sns-algorithm",
    storageBucket: "sns-algorithm.firebasestorage.app",
    messagingSenderId: "265158855497",
    appId: "1:265158855497:web:3fe13f523f98cc819e2898",
    measurementId: "G-9RMNTJTPX2"
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

export { db };

pub mod config;
pub mod db;
pub mod error;
pub mod neoconf;
pub mod ollama;
pub mod processus;
pub mod providers;
pub mod secrets;

pub use config::{APP_NAME, APP_VERSION, Settings};
pub use db::Db;
pub use error::AppError;
pub use secrets::{chiffrer, dechiffrer, est_secret, keystore_path, PREFIX};
